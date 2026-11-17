"""Job orchestration, independent of Celery so it can run from the CLI and tests.

source job  --ingest-->  N clip jobs  --render-->  awaiting_approval
            --approve-->  publishing  --publish-->  published
"""
from __future__ import annotations

import logging
import statistics
import traceback
from pathlib import Path

from app.config import get_settings
from app.services import highlight_ai, ingest, llm, rights, telegram_bot, transcriber, video_editor
from app.utils import campaigns, runtime_settings
from app.utils.job_store import get_store

log = logging.getLogger(__name__)


_pools: dict[str, "ThreadPoolExecutor"] = {}


def _pool(name: str, workers: int):
    from concurrent.futures import ThreadPoolExecutor

    if name not in _pools:
        _pools[name] = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
    return _pools[name]


def _dispatch(task_name: str, job_id: str) -> None:
    mode = get_settings().mode
    if mode == "thread":
        fn = {"ingest_source_task": ingest_source, "render_clip_task": render_clip,
              "publish_clip_task": publish_clip, "record_live_task": record_live}[task_name]
        # one heavy video job at a time; live recorders and light work get their own pools
        pool = _pool("live", 2) if task_name == "record_live_task" else \
            _pool("work", 1) if task_name in ("ingest_source_task", "render_clip_task") else _pool("publish", 1)
        pool.submit(fn, job_id)
        return
    from app import celery_worker

    getattr(celery_worker, task_name).delay(job_id)


def job_options(job: dict) -> dict:
    """Per-job choices (from the UI/API), falling back to settings."""
    s = get_settings()
    o = dict((job.get("meta") or {}).get("options") or {})
    o.setdefault("formats", [f.strip() for f in s.output_formats.split(",") if f.strip()])
    o.setdefault("clips", s.clips_per_source)
    o.setdefault("clip_seconds", s.clip_target_s)
    o.setdefault("captions", s.captions_enabled)
    o.setdefault("caption_style", s.caption_style)
    o.setdefault("hook", s.hook_overlay)
    o.setdefault("progress_bar", s.progress_bar)
    o.setdefault("jump_cuts", s.jump_cuts)
    o.setdefault("zoom", s.zoom_punch)
    o.setdefault("cover_intro", s.cover_intro)
    o.setdefault("campaign", None)
    return o


BOOL_OPTS = ("captions", "hook", "progress_bar", "jump_cuts", "zoom", "cover_intro")


def clean_options(formats=None, clips=None, clip_seconds=None, captions=None, caption_style=None,
                  hook=None, progress_bar=None, jump_cuts=None, zoom=None, cover_intro=None,
                  campaign=None) -> dict:
    s = get_settings()
    o: dict = {}
    if formats:
        fs = [f.strip().lower() for f in (formats.split(",") if isinstance(formats, str) else formats) if f.strip()]
        for f in fs:
            video_editor.format_size(f)  # raises on unknown
        o["formats"] = list(dict.fromkeys(fs))
    if clips:
        o["clips"] = max(1, min(10, int(clips)))
    if clip_seconds:
        o["clip_seconds"] = max(s.clip_min_s, min(s.clip_max_s, float(clip_seconds)))
    if captions is not None:
        o["captions"] = bool(captions)
    if caption_style:
        if caption_style not in transcriber.CAPTION_STYLES:
            raise ValueError(f"caption_style must be one of {', '.join(transcriber.CAPTION_STYLES)}")
        o["caption_style"] = caption_style
    if hook is not None:
        o["hook"] = bool(hook)
    if progress_bar is not None:
        o["progress_bar"] = bool(progress_bar)
    for k, v in (("jump_cuts", jump_cuts), ("zoom", zoom), ("cover_intro", cover_intro)):
        if v is not None:
            o[k] = bool(v)
    if campaign:
        if not campaigns.get(campaign):
            raise ValueError(f"unknown campaign {campaign!r}")
        o["campaign"] = campaign
    return o


def _notify(text: str) -> None:
    s = get_settings()
    if s.telegram_bot_token and s.telegram_owner_chat_id:
        try:
            telegram_bot.send_text(text)
        except Exception as e:  # notification failure must not fail the job
            log.warning("telegram notify failed: %s", e)


# ---------------------------------------------------------------- creation
def submit(source_type: str, source: str, raw_path: Path | None = None, title: str | None = None,
           options: dict | None = None) -> str:
    store = get_store()
    task = "record_live_task" if source_type == "live" else "ingest_source_task"
    job_id = store.create("source", source_type=source_type, source=source,
                          raw_path=str(raw_path) if raw_path else None, title=title,
                          meta={"options": options or {}})
    _dispatch(task, job_id)
    return job_id


def submit_link(url: str, text: str = "") -> str:
    """Used by the Telegram bot: a link that is live goes to the recorder, anything else is downloaded.
    Format words in the message ("landscape", "square", ...) choose the output shapes."""
    words = set(text.lower().replace(",", " ").split())
    fmts = [f for f in video_editor.FORMATS if f in words or (f == "landscape" and "horizontal" in words)]
    opts = clean_options(fmts) if fmts else {}
    job_id = submit("url", url, options=opts)
    shapes = ", ".join(opts.get("formats") or job_options({})["formats"])
    return f"Queued {url} ({shapes}) as job {job_id}. I'll send clips here for approval."


# ---------------------------------------------------------------- ingest
def ingest_source(job_id: str) -> list[str]:
    s, store = get_settings(), get_store()
    job = store.get(job_id)
    try:
        raw = Path(job["raw_path"]) if job["raw_path"] else None
        title = job["title"]
        if job["source_type"] == "url":
            store.update(job_id, status="downloading")
            try:
                raw, meta = ingest.download_url(job["source"], s.raw_dir)
            except ValueError as e:
                if "live stream" in str(e):  # link turned out to be live: switch to recorder
                    store.update(job_id, source_type="live", status="queued")
                    _dispatch("record_live_task", job_id)
                    return []
                raise
            title = title or meta.get("title")
            risk = rights.assess(meta.pop("rights_info", None), "url")
            store.update(job_id, raw_path=str(raw), title=title, meta={"source": meta, "rights": risk})
            if risk["risk"] == "high":
                _notify(f"{rights.summary_line(risk)} for '{title}'.\n" + "\n".join(f"• {r}" for r in risk["reasons"][:3]))
        elif job["source_type"] == "upload" and not (job.get("meta") or {}).get("rights"):
            store.update(job_id, meta={"rights": rights.assess({"title": title}, "upload")})
        if raw is None or not raw.exists():
            raise FileNotFoundError(f"raw file missing for job {job_id}")
        store.update(job_id, status="analyzing")
        opts = job_options(job)
        rs = runtime_settings.load()
        use_rank = llm.enabled(rs) and rs["llm_rank"]
        hls = highlight_ai.find_highlights(raw, s.temp_dir, target_s=opts["clip_seconds"], min_s=s.clip_min_s,
                                           max_s=s.clip_max_s, top_n=opts["clips"] * (3 if use_rank else 1))
        if use_rank and len(hls) > opts["clips"]:
            hls = llm_pick(raw, hls, opts["clips"], title, rs, job_id)
        else:
            hls = hls[:opts["clips"]]
        clip_ids = _make_clips(job_id, job, raw, hls, title, opts)
        store.update(job_id, status="done")
        for cid in clip_ids:
            _dispatch("render_clip_task", cid)
        return clip_ids
    except Exception as e:
        log.exception("ingest failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Job {job_id} failed while ingesting: {str(e)[:400]}\nRetry it from the web UI.")
        return []


def _transcript(raw: Path, start: float, end: float, rs: dict) -> list:
    s = get_settings()
    return transcriber.transcribe_words(raw, s.temp_dir, start, end - start, model=rs["whisper_model"],
                                        device=s.whisper_device, compute_type=s.whisper_compute_type,
                                        language=s.whisper_language)


def llm_pick(raw: Path, hls: list, n: int, title: str | None, rs: dict, job_id: str) -> list:
    """Re-rank audio candidates with the LLM using what is said in each; falls back to audio order."""
    import numpy as np

    try:
        cands = []
        for i, h in enumerate(hls):
            words = _transcript(raw, h.start, h.end, rs)
            cands.append({"id": i, "start": h.start, "end": h.end, "score": h.score,
                          "transcript": " ".join(w.text for w in words)})
        llm_scores = llm.rank_moments(cands, title or raw.stem, rs)
        audio = np.array([h.score for h in hls], dtype=float)
        a = (audio - audio.mean()) / (audio.std() or 1.0)
        l_raw = np.array([llm_scores.get(i, 5.0) for i in range(len(hls))])
        lz = (l_raw - l_raw.mean()) / (l_raw.std() or 1.0)
        order = np.argsort(-(0.5 * a + 0.5 * lz))[:n]
        get_store().update(job_id, meta={"llm_rank": {str(i): llm_scores.get(i) for i in range(len(hls))}})
        return sorted((hls[int(i)] for i in order), key=lambda h: h.start)
    except Exception as e:
        log.warning("LLM ranking skipped: %s", e)
        get_store().update(job_id, meta={"llm_error": str(e)[:300]})
        return sorted(sorted(hls, key=lambda h: -h.score)[:n], key=lambda h: h.start)


def _make_clips(parent_id: str, job: dict, raw: Path, hls, title: str | None, opts: dict,
                name_prefix: str | None = None) -> list[str]:
    """One clip job per (highlight, format)."""
    store = get_store()
    base_title = title or Path(job["source"] or raw.name).stem
    ids = []
    for n, h in enumerate(hls, 1):
        t = name_prefix or (base_title if len(hls) == 1 else f"{base_title} (part {n})")
        for fmt in opts["formats"]:
            ids.append(store.create("clip", parent_id=parent_id, source_type=job["source_type"],
                                    source=job["source"], raw_path=str(raw), start_s=h.start, end_s=h.end,
                                    score=h.score, title=t,
                                    meta={"format": fmt, "captions": opts["captions"],
                                          "caption_style": opts.get("caption_style", "pop"),
                                          "hook": opts.get("hook", True),
                                          "progress_bar": opts.get("progress_bar", True),
                                          "jump_cuts": opts.get("jump_cuts", True),
                                          "zoom": opts.get("zoom", True),
                                          "cover_intro": opts.get("cover_intro", True),
                                          "campaign": opts.get("campaign"),
                                          "audio_score": round(float(h.score), 3)}))
    return ids


# ---------------------------------------------------------------- render
def caption_text(title: str, meta: dict | None = None) -> str:
    meta = meta or {}
    md = meta.get("llm") or {}
    tags = (" ".join(md.get("hashtags") or []) or runtime_settings.load().get("default_hashtags")
            or get_settings().default_hashtags)
    extra = campaigns.caption_extras(meta.get("campaign"))
    caption = meta.get("caption_custom") if meta.get("caption_custom") is not None else md.get("caption")
    body = "\n\n".join(x for x in (title, caption) if x)
    return f"{body}\n\n{tags} {extra}".strip()


def virality_score(audio_score: float, llm_score: float | None = None) -> int:
    """0-100. Audio excitement (z-score) through a sigmoid, averaged with the LLM's guess when there is one."""
    import math

    a = 100 / (1 + math.exp(-1.6 * float(audio_score or 0)))
    if llm_score is None:
        return int(round(a))
    return int(round(0.4 * a + 0.6 * max(0.0, min(100.0, float(llm_score)))))


def fallback_hook(title: str | None) -> str | None:
    """Without an LLM: a short version of the source title (first clause, <= 7 words)."""
    if not title:
        return None
    import re

    t = re.split(r"[|\-–—:(\[]", title)[0].strip() or title
    words = t.split()
    return " ".join(words[:7]) + ("…" if len(words) > 7 else "")


def zoom_times(words: list, keywords: set[str], duration: float) -> list[tuple[float, float]]:
    """Punch-ins on keywords (LLM), else on emphasised words, else a gentle rhythm every ~5 s."""
    kws = {k.lower().strip(".,!?") for k in keywords}
    hits = [w for w in words if w.text.lower().strip(".,!?") in kws] or \
           [w for w in words if w.text.endswith("!") or (w.text.isupper() and len(w.text) > 2)]
    spans = [(max(0.0, w.start - 0.05), min(duration, w.end + 0.6)) for w in hits]
    if not spans and duration > 8:
        spans = [(t, min(duration, t + 1.2)) for t in [5.0 * k for k in range(1, int(duration // 5))]]
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a - merged[-1][1] < 1.5:
            merged[-1][1] = max(merged[-1][1], b)
        elif not merged or a - merged[-1][1] >= 1.5:
            merged.append([a, b])
    return [(a, b) for a, b in merged][:12]


def _remap_words(words: list, keep) -> list:
    out = []
    for w in words:
        a, b = video_editor.remap_time(w.start, keep), video_editor.remap_time(w.end, keep)
        if a is None:
            continue
        out.append(transcriber.Word(a, b if b is not None else a + 0.3, w.text))
    return out


def make_cover(job_id: str, raw: Path, start: float, end: float, out_w: int, out_h: int, crop_x,
               text: str | None) -> tuple[Path, float]:
    """Cover image: the user's own picture, or their chosen moment, or the sharpest frame; with the hook on it."""
    s, store = get_settings(), get_store()
    meta = store.get(job_id)["meta"]
    dst = s.processed_dir / f"{job_id}.jpg"
    cover_ass = transcriber.write_ass([], s.temp_dir / f"{job_id}_cover.ass", out_w, out_h, font=s.caption_font,
                                      style="none", hook=text, hook_seconds=5, animate_hook=False, hook_scale=1.6)
    try:
        custom = meta.get("cover_image")
        if custom and Path(custom).exists():
            return video_editor.cover_from_image(Path(custom), dst, out_w, out_h, cover_ass), start
        t = meta.get("cover_time")
        t = start + float(t) if t is not None and 0 <= float(t) <= end - start else \
            video_editor.pick_cover_time(raw, start, end)
        return video_editor.make_cover(raw, dst, t, out_w, out_h, crop_x, cover_ass), t
    finally:
        if cover_ass:
            cover_ass.unlink(missing_ok=True)


def render_clip(job_id: str) -> Path | None:
    s, store = get_settings(), get_store()
    if not store.transition(job_id, ("queued", "failed"), "rendering"):
        return None
    job = store.get(job_id)
    try:
        raw = Path(job["raw_path"])
        start, end = float(job["start_s"]), float(job["end_s"])
        store.update(job_id, meta={"caption_error": None, "llm_error": None})  # clear notes from earlier renders
        meta = store.get(job_id)["meta"]
        fmt = meta.get("format", "vertical")
        out_w, out_h = video_editor.format_size(fmt)
        style = meta.get("caption_style", s.caption_style) if meta.get("captions", s.captions_enabled) else "none"
        if style != "none" and not video_editor.can_burn_captions():
            style = "none"
            store.update(job_id, meta={"caption_error": "ffmpeg was built without libass; install ffmpeg-full "
                                                        "(brew install ffmpeg-full) to get captions"})
        rs = runtime_settings.load()
        use_llm_meta = llm.enabled(rs) and rs["llm_titles"]
        want_cuts = meta.get("jump_cuts", s.jump_cuts)
        words: list = []
        if style != "none" or use_llm_meta or want_cuts:
            try:
                words = _transcript(raw, start, end, rs)
            except Exception as e:  # captions are optional; never lose the clip over them
                log.warning("transcription failed for %s: %s", job_id, e)
                store.update(job_id, meta={"caption_error": f"transcription failed: {str(e)[:250]}"})
        parent = store.get(job["parent_id"]) if job.get("parent_id") else None
        source_title = (parent or {}).get("title") or job["title"] or ""
        md: dict = dict(meta.get("llm") or {})
        if use_llm_meta and (not md or meta.get("llm_refresh")):
            try:
                used = [x for c in (store.children(job["parent_id"]) if job.get("parent_id") else [])
                        if c["id"] != job_id for x in (c["meta"].get("hook_text"), c["title"])]
                md = llm.clip_metadata(" ".join(w.text for w in words), source_title, fmt, rs, avoid=used[:12])
                store.update(job_id, meta={"llm": md, "llm_refresh": False})
            except Exception as e:
                log.warning("LLM metadata skipped for %s: %s", job_id, e)
                store.update(job_id, meta={"llm_error": str(e)[:300]})
        if md.get("title") and not meta.get("title_locked"):
            store.update(job_id, title=md["title"])
            job["title"] = md["title"]
        hook = None
        if meta.get("hook", s.hook_overlay):
            hook = meta.get("hook_custom") or md.get("hook") or fallback_hook(source_title)
        duration = end - start
        keep = None
        if want_cuts and len(words) >= 6:
            keep = video_editor.jump_cut_spans(words, duration,
                                               gap_is_dead=video_editor.dead_gap_checker(raw, start, duration))
        out_words = _remap_words(words, keep) if keep else words
        out_dur = sum(b - a for a, b in keep) if keep else duration
        zooms = zoom_times(out_words, set(md.get("keywords") or []), out_dur) if meta.get("zoom", s.zoom_punch) else None
        ass = transcriber.write_ass(out_words, s.temp_dir / f"{job_id}.ass", out_w, out_h, s.caption_words_per_line,
                                    s.caption_font, style=style, hook=hook, hook_seconds=s.hook_seconds,
                                    keywords=set(md.get("keywords") or []),
                                    watermark=(rs.get("watermark") or "").strip() or None)
        out = s.processed_dir / f"{job_id}.mp4"
        info = video_editor.probe(raw)
        crop_x = None
        if info.aspect > (out_w / out_h) * 1.01:
            crop_x = video_editor.find_crop_x(raw, start, duration, info, target_aspect=out_w / out_h)
        # Cover first: it is also the clip's opening frames, so every platform shows it as the thumbnail.
        cover, cover_t = None, start
        try:
            cover, cover_t = make_cover(job_id, raw, start, end, out_w, out_h, crop_x,
                                        meta.get("cover_text") or hook)
        except Exception as e:
            log.warning("cover failed for %s: %s", job_id, e)
        intro = cover if (cover and meta.get("cover_intro", s.cover_intro)) else None
        video_editor.render_clip(raw, out, start, end, ass_file=ass, out_w=out_w, out_h=out_h, crop_x=crop_x,
                                 progress_bar=meta.get("progress_bar", s.progress_bar), keep=keep,
                                 zoom_times=zooms, intro_image=intro)
        if ass:
            ass.unlink(missing_ok=True)
        if cover:
            store.update(job_id, meta={"cover_path": str(cover),
                                       "cover_offset_ms": 0 if intro else int((cover_t - start) * 1000)})
        score = virality_score(meta.get("audio_score", job.get("score") or 0), md.get("virality"))
        store.update(job_id, output_path=str(out), score=score,
                     meta={"caption_words": len(words) if style != "none" else 0, "hook_text": hook,
                           "virality": score, "duration": round(out_dur, 1),
                           "cuts": (len(keep) - 1 + (keep[0][0] > 0) + (keep[-1][1] < duration - 0.01)) if keep else 0,
                           "seconds_removed": round(duration - out_dur, 1), "zooms": len(zooms or [])})
        store.transition(job_id, "rendering", "awaiting_approval")
        _send_for_review(job_id)
        return out
    except Exception as e:
        log.exception("render failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Clip {job_id} failed to render: {e}")
        return None


def _send_for_review(job_id: str) -> None:
    s, store = get_settings(), get_store()
    if not (s.telegram_bot_token and s.telegram_owner_chat_id):
        return
    job = store.get(job_id)
    meta = job["meta"]
    out = Path(job["output_path"])
    send = out
    if out.stat().st_size > s.max_output_mb * 1024 * 1024:
        send = video_editor.make_preview(out, s.temp_dir / f"{job_id}_preview.mp4", s.max_output_mb - 3)
    parent = store.get(job["parent_id"]) if job.get("parent_id") else None
    risk = rights.summary_line((parent or {}).get("meta", {}).get("rights"))
    camp = campaigns.get(meta.get("campaign"))
    extras = []
    if meta.get("cuts"):
        extras.append(f"✂️ {meta['cuts']} jump cuts, {meta.get('seconds_removed', 0):.0f}s of dead air removed")
    if camp:
        extras.append(f"💰 Campaign: {camp['name']}")
    if risk:
        extras.append(risk)
    try:
        mid = telegram_bot.send_for_approval(
            job_id, send, f"🔥 Virality {meta.get('virality', '?')}/100\n"
                          f"🪝 {meta.get('hook_text') or '(no hook)'}\n\n{caption_text(job['title'] or '', meta)}\n\n"
                          f"{meta.get('duration', 0):.0f}s {meta.get('format', '')} clip"
                          + ("\n" + "\n".join(extras) if extras else "")
                          + ("\n(preview: full quality is kept for publishing)" if send != out else ""),
            cover=Path(meta["cover_path"]) if meta.get("cover_path") else None)
        store.update(job_id, meta={"telegram_message_id": mid})
    except Exception as e:
        log.warning("telegram review send failed: %s", e)
    finally:
        if send != out:
            send.unlink(missing_ok=True)


# ---------------------------------------------------------------- editing
EDITABLE_AFTER = ("awaiting_approval", "approved", "failed", "scheduled")


def edit_clip(job_id: str, title: str | None = None, hook: str | None = None, caption: str | None = None,
              cover_text: str | None = None, cover_time: float | None = None,
              cover_image: bytes | None = None, cover_reset: bool = False) -> str:
    """Change a clip's title/caption (instant) or its hook/cover (re-renders the clip, ~1 min)."""
    s, store = get_settings(), get_store()
    job = store.get(job_id)
    if not job or job["kind"] != "clip":
        raise ValueError("unknown clip")
    if job["status"] not in EDITABLE_AFTER:
        raise ValueError(f"clip is {job['status']}; wait until it's ready")
    changed, rerender = [], False
    if title is not None and title.strip():
        store.update(job_id, title=title.strip()[:100], meta={"title_locked": True})
        changed.append("title")
    if caption is not None:
        store.update(job_id, meta={"caption_custom": caption.strip()[:2000]})
        changed.append("caption")
    if hook is not None:
        store.update(job_id, meta={"hook_custom": " ".join(hook.split())[:80] or None, "hook": bool(hook.strip())})
        changed.append("hook")
        rerender = True
    if cover_text is not None:
        store.update(job_id, meta={"cover_text": cover_text.strip()[:80] or None})
        changed.append("cover text")
        rerender = True
    if cover_time is not None:
        store.update(job_id, meta={"cover_time": max(0.0, float(cover_time)), "cover_image": None})
        changed.append("cover frame")
        rerender = True
    if cover_image:
        p = s.processed_dir / f"{job_id}_custom_cover.jpg"
        p.write_bytes(cover_image)
        store.update(job_id, meta={"cover_image": str(p)})
        changed.append("cover image")
        rerender = True
    if cover_reset:
        store.update(job_id, meta={"cover_image": None, "cover_time": None, "cover_text": None})
        changed.append("cover")
        rerender = True
    if not changed:
        return "Nothing to change"
    if rerender:
        store.update(job_id, status="queued", error=None)
        _dispatch("render_clip_task", job_id)
        return f"Updated {', '.join(changed)}. Re-making the clip; the new version will appear in about a minute."
    return f"Updated {', '.join(changed)}."


# ---------------------------------------------------------------- approval
def approve(job_id: str, publish_at: str | None = None) -> str:
    """publish_at: optional ISO time (UTC or with offset). Until then the clip waits as 'scheduled'."""
    from datetime import datetime, timezone

    from app.services import social_publisher as sp

    store = get_store()
    job = store.get(job_id)
    if not job:
        return "Unknown job"
    if not any(sp.publishers_configured().values()):
        if store.transition(job_id, "awaiting_approval", "approved"):
            return ("Approved and saved. Connect YouTube, Instagram or TikTok in the web UI (Publishing) "
                    "to post automatically; until then download it from the UI.")
        return f"Already {job['status']}"
    if publish_at:
        when = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > datetime.now(timezone.utc):
            if store.transition(job_id, ("awaiting_approval", "approved", "scheduled"), "scheduled"):
                store.update(job_id, meta={"publish_at": when.astimezone(timezone.utc).isoformat()})
                return f"Scheduled for {when.isoformat(timespec='minutes')}"
            return f"Already {job['status']}"
    if store.transition(job_id, ("awaiting_approval", "approved", "scheduled"), "publishing"):
        _dispatch("publish_clip_task", job_id)
        return "Approved, publishing now"
    job = store.get(job_id)
    return f"Already {job['status']}"


def run_due_schedules() -> int:
    """Publish scheduled clips whose time has come. Called every 30 s by the serve/poll loop."""
    from datetime import datetime, timezone

    store, n = get_store(), 0
    now = datetime.now(timezone.utc)
    for job in store.by_status("scheduled"):
        at = job["meta"].get("publish_at")
        try:
            due = not at or datetime.fromisoformat(at) <= now
        except ValueError:
            due = True
        if due and store.transition(job["id"], "scheduled", "publishing"):
            _dispatch("publish_clip_task", job["id"])
            n += 1
    return n


def reject(job_id: str) -> str:
    store = get_store()
    job = store.get(job_id)
    if job and store.transition(job_id, "awaiting_approval", "rejected"):
        if job.get("output_path"):
            Path(job["output_path"]).unlink(missing_ok=True)
        return "Rejected and deleted"
    return f"Already {job['status']}" if job else "Unknown job"


# ---------------------------------------------------------------- publish
def publish_clip(job_id: str) -> dict:
    from app.services import social_publisher as sp

    store = get_store()
    job = store.get(job_id)
    if not job or job["status"] != "publishing":
        return {}
    meta = job["meta"]
    video = Path(job["output_path"])
    caption = caption_text(job["title"] or "", meta)
    configured = sp.publishers_configured()
    results, errors = {}, []
    if not any(configured.values()):
        errors.append("no publisher configured (connect YouTube, Instagram or TikTok in Publishing)")
    cover = Path(meta["cover_path"]) if meta.get("cover_path") else None
    # Idempotent: a retry skips platforms that already succeeded.
    if configured["youtube"] and not meta.get("youtube_id"):
        try:
            is_short = meta.get("format", "vertical") != "landscape" and float(meta.get("duration") or 0) <= 180
            results["youtube_id"] = sp.upload_youtube(video, job["title"] or "Clip", caption, shorts=is_short,
                                                      thumbnail=cover)
            results["youtube_short"] = is_short
        except Exception as e:
            errors.append(f"YouTube: {e}")
    if configured["instagram"] and not meta.get("instagram_id"):
        try:
            results["instagram_id"] = sp.upload_instagram(video, caption, thumb_offset_ms=meta.get("cover_offset_ms"))
            results["instagram_permalink"] = sp.instagram_permalink(results["instagram_id"])
        except Exception as e:
            errors.append(f"Instagram: {e}")
    if configured.get("tiktok") and not meta.get("tiktok_id"):
        try:
            results["tiktok_id"] = sp.upload_tiktok(video, caption, cover_ms=meta.get("cover_offset_ms") or 0)
        except Exception as e:
            errors.append(f"TikTok: {e}")
    store.update(job_id, meta=results)
    if errors:
        store.update(job_id, status="failed", error="; ".join(errors))
        _notify(f"⚠️ Clip {job_id} publish problems: {'; '.join(errors)}")
    else:
        store.update(job_id, status="published")
        links = campaigns.post_links({**meta, **results})
        camp = campaigns.get(meta.get("campaign"))
        msg = "✅ Published:\n" + "\n".join(f"{l['platform']}: {l['url']}" for l in links)
        if camp:
            msg += f"\n\n💰 Submit these links to {camp['name']}" + (f": {camp['url']}" if camp.get("url") else "")
        _notify(msg)
    return results


def refresh_stats() -> dict:
    """Read view counts of published clips and estimate campaign earnings."""
    from app.services import social_publisher as sp

    store = get_store()
    clips = store.by_status("published")
    yt_ids = [c["meta"]["youtube_id"] for c in clips if c["meta"].get("youtube_id")]
    errors = []
    yt = {}
    try:
        yt = sp.youtube_views(yt_ids)
    except Exception as e:
        errors.append(f"YouTube views: {str(e)[:150]} (reconnect YouTube to allow reading views)")
    total_views, total_est = 0, 0.0
    for c in clips:
        m = c["meta"]
        views = yt.get(m.get("youtube_id"), m.get("views_youtube") or 0)
        ig = m.get("views_instagram") or 0
        if m.get("instagram_id"):
            try:
                ig = sp.instagram_views(m["instagram_id"]) or ig
            except Exception as e:
                errors.append(f"Instagram views: {str(e)[:150]}")
        camp = campaigns.get(m.get("campaign"))
        est = campaigns.estimate(views + ig, camp["cpm"]) if camp else 0.0
        store.update(c["id"], meta={"views_youtube": views, "views_instagram": ig, "earnings_est": est})
        total_views += views + ig
        total_est += est
    return {"clips": len(clips), "views": total_views, "earnings_est": round(total_est, 2),
            "errors": list(dict.fromkeys(errors))}


def start_scheduler(interval_s: float = 30.0) -> None:
    """Background loop that publishes scheduled clips on time (serve / poll processes)."""
    import threading
    import time

    if _pools.get("_scheduler"):
        return

    def loop():
        while True:
            try:
                run_due_schedules()
            except Exception:
                log.exception("scheduler tick failed")
            time.sleep(interval_s)

    _pools["_scheduler"] = threading.Thread(target=loop, daemon=True, name="scheduler")
    _pools["_scheduler"].start()


def check_text(url: str) -> str:
    """Copyright check as a chat message (Telegram /check)."""
    try:
        r = rights.check_url(url)
    except Exception as e:
        return f"Couldn't check that link: {str(e)[:200]}"
    lines = [rights.summary_line(r), f"{r.get('title') or url}" + (f" — {r['uploader']}" if r.get("uploader") else ""),
             ""] + [f"• {x}" for x in r["reasons"]] + ["", "What to do:"] + [f"• {x}" for x in r["advice"]]
    lines.append("\n(An estimate from public info, not legal advice.)")
    return "\n".join(lines)


# ---------------------------------------------------------------- live
def record_live(job_id: str, realtime_input: bool = False) -> list[str]:
    """Record a live stream in segments; every segment that is much louder/busier
    than the stream's recent baseline becomes a clip job while recording continues."""
    s, store = get_settings(), get_store()
    job = store.get(job_id)
    out_dir = s.live_dir / job_id
    opts = job_options(job)
    clip_ids: list[str] = []
    try:
        store.update(job_id, status="recording")
        try:
            store.update(job_id, meta={"rights": rights.check_url(job["source"])})
        except Exception as e:
            log.info("rights check skipped for live %s: %s", job_id, e)
        proc = ingest.record_live(job["source"], out_dir, segment_s=s.live_segment_s,
                                  max_s=s.live_max_hours * 3600, realtime_input=realtime_input)

        def stop() -> bool:
            return bool((store.get(job_id) or {}).get("meta", {}).get("stop_requested"))

        history: list[float] = []
        for seg in ingest.finished_segments(out_dir, proc, poll_s=min(5.0, s.live_segment_s / 4), should_stop=stop):
            try:
                score = highlight_ai.peak_score(seg, s.temp_dir)
            except Exception as e:
                log.warning("segment %s unreadable: %s", seg.name, e)
                continue
            baseline = statistics.median(history[-10:]) if len(history) >= 3 else None
            history.append(score)
            is_hl = baseline is not None and score > s.live_highlight_ratio * baseline
            log.info("live %s %s score=%.4f baseline=%s highlight=%s", job_id, seg.name, score, baseline, is_hl)
            if not is_hl:
                continue
            h = highlight_ai.find_highlights(seg, s.temp_dir, opts["clip_seconds"], s.clip_min_s, s.clip_max_s,
                                             top_n=1)[0]
            h.score = score
            new = _make_clips(job_id, job, seg, [h], job["title"], opts,
                              name_prefix=f"{job['title'] or 'Live highlight'} #{len(clip_ids) // len(opts['formats']) + 1}")
            clip_ids += new
            for cid in new:
                _dispatch("render_clip_task", cid)
        store.update(job_id, status="done", meta={"segments": len(history), "clips": len(clip_ids)})
    except Exception as e:
        log.exception("live recording failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Live job {job_id} stopped: {e}")
    return clip_ids
