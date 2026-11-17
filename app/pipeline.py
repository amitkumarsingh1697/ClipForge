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
from app.services import highlight_ai, ingest, llm, telegram_bot, transcriber, video_editor
from app.utils import runtime_settings
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
    return o


def clean_options(formats=None, clips=None, clip_seconds=None, captions=None, caption_style=None,
                  hook=None, progress_bar=None) -> dict:
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
            store.update(job_id, raw_path=str(raw), title=title, meta={"source": meta})
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
                                          "audio_score": round(float(h.score), 3)}))
    return ids


# ---------------------------------------------------------------- render
def caption_text(title: str, meta: dict | None = None) -> str:
    md = (meta or {}).get("llm") or {}
    tags = " ".join(md.get("hashtags") or []) or get_settings().default_hashtags
    body = "\n\n".join(x for x in (title, md.get("caption")) if x)
    return f"{body}\n\n{tags}".strip()


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


def render_clip(job_id: str) -> Path | None:
    s, store = get_settings(), get_store()
    if not store.transition(job_id, ("queued", "failed"), "rendering"):
        return None
    job = store.get(job_id)
    try:
        raw = Path(job["raw_path"])
        start, end = float(job["start_s"]), float(job["end_s"])
        meta = job["meta"]
        fmt = meta.get("format", "vertical")
        out_w, out_h = video_editor.format_size(fmt)
        style = meta.get("caption_style", s.caption_style) if meta.get("captions", s.captions_enabled) else "none"
        if style != "none" and not video_editor.can_burn_captions():
            style = "none"
            store.update(job_id, meta={"caption_error": "ffmpeg was built without libass; install ffmpeg-full "
                                                        "(brew install ffmpeg-full) to get captions"})
        rs = runtime_settings.load()
        use_llm_meta = llm.enabled(rs) and rs["llm_titles"]
        words: list = []
        if style != "none" or use_llm_meta:
            try:
                words = _transcript(raw, start, end, rs)
            except Exception as e:  # captions are optional; never lose the clip over them
                log.warning("transcription failed for %s: %s", job_id, e)
                store.update(job_id, meta={"caption_error": f"transcription failed: {str(e)[:250]}"})
        parent = store.get(job["parent_id"]) if job.get("parent_id") else None
        source_title = (parent or {}).get("title") or job["title"] or ""
        md: dict = {}
        if use_llm_meta:
            try:
                md = llm.clip_metadata(" ".join(w.text for w in words), source_title, fmt, rs)
                if md.get("title"):
                    store.update(job_id, title=md["title"])
                    job["title"] = md["title"]
                store.update(job_id, meta={"llm": md})
            except Exception as e:
                log.warning("LLM metadata skipped for %s: %s", job_id, e)
                store.update(job_id, meta={"llm_error": str(e)[:300]})
        hook = (md.get("hook") or fallback_hook(source_title)) if meta.get("hook", s.hook_overlay) else None
        ass = transcriber.write_ass(words, s.temp_dir / f"{job_id}.ass", out_w, out_h, s.caption_words_per_line,
                                    s.caption_font, style=style, hook=hook, hook_seconds=s.hook_seconds,
                                    keywords=set(md.get("keywords") or []))
        out = s.processed_dir / f"{job_id}.mp4"
        info = video_editor.probe(raw)
        crop_x = None
        if info.aspect > (out_w / out_h) * 1.01:
            crop_x = video_editor.find_crop_x(raw, start, end - start, info, target_aspect=out_w / out_h)
        video_editor.render_clip(raw, out, start, end, ass_file=ass, out_w=out_w, out_h=out_h, crop_x=crop_x,
                                 progress_bar=meta.get("progress_bar", s.progress_bar))
        if ass:
            ass.unlink(missing_ok=True)
        # Cover image: sharpest frame + hook title (used as Instagram cover, shown in UI/Telegram)
        cover = None
        try:
            t = video_editor.pick_cover_time(raw, start, end)
            cover_ass = transcriber.write_ass([], s.temp_dir / f"{job_id}_cover.ass", out_w, out_h,
                                              font=s.caption_font, style="none", hook=hook, hook_seconds=5,
                                              animate_hook=False)
            cover = video_editor.make_cover(raw, s.processed_dir / f"{job_id}.jpg", t, out_w, out_h, crop_x, cover_ass)
            if cover_ass:
                cover_ass.unlink(missing_ok=True)
            store.update(job_id, meta={"cover_path": str(cover), "cover_offset_ms": int((t - start) * 1000)})
        except Exception as e:
            log.warning("cover failed for %s: %s", job_id, e)
        score = virality_score(meta.get("audio_score", job.get("score") or 0), md.get("virality"))
        store.update(job_id, output_path=str(out), score=score,
                     meta={"caption_words": len(words) if style != "none" else 0, "hook_text": hook,
                           "virality": score, "duration": round(end - start, 1)})
        store.transition(job_id, "rendering", "awaiting_approval")
        if s.telegram_bot_token and s.telegram_owner_chat_id:
            send = out
            if out.stat().st_size > s.max_output_mb * 1024 * 1024:
                send = video_editor.make_preview(out, s.temp_dir / f"{job_id}_preview.mp4", s.max_output_mb - 3)
            fresh = store.get(job_id)
            mid = telegram_bot.send_for_approval(
                job_id, send, f"🔥 Virality {score}/100\n{caption_text(fresh['title'] or '', fresh['meta'])}\n\n"
                              f"{end-start:.0f}s {fmt} clip from {job['source'] or raw.name}"
                              + ("\n(preview: full quality is kept for publishing)" if send != out else ""),
                cover=cover)
            if send != out:
                send.unlink(missing_ok=True)
            store.update(job_id, meta={"telegram_message_id": mid})
        return out
    except Exception as e:
        log.exception("render failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Clip {job_id} failed to render: {e}")
        return None


# ---------------------------------------------------------------- approval
def approve(job_id: str) -> str:
    from app.services import social_publisher as sp

    store = get_store()
    if not any(sp.publishers_configured().values()):
        job = store.get(job_id)
        if job and store.transition(job_id, "awaiting_approval", "approved"):
            return ("Approved and saved. Connect YouTube or Instagram in the web UI (Publishing) "
                    "to post automatically; until then download it from the UI.")
        return f"Already {job['status']}" if job else "Unknown job"
    if store.transition(job_id, ("awaiting_approval", "approved"), "publishing"):
        _dispatch("publish_clip_task", job_id)
        return "Approved, publishing now"
    job = store.get(job_id)
    return f"Already {job['status']}" if job else "Unknown job"


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
        errors.append("no publisher configured (authorise YouTube and/or set Instagram credentials)")
    # Idempotent: a retry skips platforms that already succeeded.
    if configured["youtube"] and not meta.get("youtube_id"):
        try:
            is_short = meta.get("format", "vertical") != "landscape" and float(meta.get("duration") or 0) <= 180
            results["youtube_id"] = sp.upload_youtube(video, job["title"] or "Clip", caption, shorts=is_short,
                                                      thumbnail=Path(meta["cover_path"]) if meta.get("cover_path") else None)
        except Exception as e:
            errors.append(f"YouTube: {e}")
    if configured["instagram"] and not meta.get("instagram_id"):
        try:
            results["instagram_id"] = sp.upload_instagram(video, caption, thumb_offset_ms=meta.get("cover_offset_ms"))
        except Exception as e:
            errors.append(f"Instagram: {e}")
    store.update(job_id, meta=results)
    if errors:
        store.update(job_id, status="failed", error="; ".join(errors))
        _notify(f"⚠️ Clip {job_id} publish problems: {'; '.join(errors)}")
    else:
        store.update(job_id, status="published")
        links = []
        if results.get("youtube_id"):
            links.append(f"https://youtu.be/{results['youtube_id']}")
        if results.get("instagram_id"):
            links.append(f"Instagram media {results['instagram_id']}")
        _notify("✅ Published: " + ", ".join(links))
    return results


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
