"""Job orchestration, independent of Celery so it can run from the CLI and tests.

source job  --ingest-->  N clip jobs  --render-->  awaiting_approval
            --approve-->  publishing  --publish-->  published
"""
from __future__ import annotations

import logging
import statistics
import time
import traceback
from pathlib import Path

from app.config import get_settings
from app.services import highlight_ai, ingest, llm, moments, music, rights, seo, telegram_bot, transcriber, video_editor
from app.utils import campaigns, progress, runtime_settings
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
    o.setdefault("tease", s.payoff_tease)
    o.setdefault("replay", s.payoff_replay)
    o.setdefault("beat", s.beat)
    o.setdefault("campaign", None)
    return o


BOOL_OPTS = ("captions", "hook", "progress_bar", "jump_cuts", "zoom", "cover_intro", "tease", "replay", "beat")


def clean_options(formats=None, clips=None, clip_seconds=None, captions=None, caption_style=None,
                  hook=None, progress_bar=None, jump_cuts=None, zoom=None, cover_intro=None,
                  campaign=None, tease=None, replay=None, beat=None) -> dict:
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
    for k, v in (("jump_cuts", jump_cuts), ("zoom", zoom), ("cover_intro", cover_intro), ("tease", tease),
                 ("replay", replay), ("beat", beat)):
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
    P = progress.Stepper(job_id)
    progress.start(job_id, progress.SOURCE_STAGES)
    try:
        raw = Path(job["raw_path"]) if job["raw_path"] else None
        title = job["title"]
        if job["source_type"] == "url":
            store.update(job_id, status="downloading")
            P("download", detail="Connecting to the site…")
            done = _most_replayed(job_id, job, P)
            if done is not None:
                return done
            try:
                raw, meta = ingest.download_url(job["source"], s.raw_dir, on_progress=P)
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
            P("download", "done", detail=f"{(meta.get('duration') or 0) / 60:.0f} min video downloaded")
        else:
            if job["source_type"] == "upload" and not (job.get("meta") or {}).get("rights"):
                store.update(job_id, meta={"rights": rights.assess({"title": title}, "upload")})
            P("download", "skipped", detail="Uploaded file, nothing to download")
        if raw is None or not raw.exists():
            raise FileNotFoundError(f"raw file missing for job {job_id}")
        store.update(job_id, status="analyzing")
        P("analyze", detail="Scanning loudness, laughter/shouting and on-screen action…")
        opts = job_options(job)
        rs = runtime_settings.load()
        use_rank = llm.enabled(rs) and rs["llm_rank"]
        hls = highlight_ai.find_highlights(raw, s.temp_dir, target_s=opts["clip_seconds"], min_s=s.clip_min_s,
                                           max_s=s.clip_max_s, top_n=opts["clips"] * (3 if use_rank else 1))
        P("analyze", "done", detail=f"{len(hls)} candidate moments found")
        if use_rank and len(hls) > opts["clips"]:
            P("rank", detail=f"AI is reading what is said in {len(hls)} moments…")
            hls = llm_pick(raw, hls, opts["clips"], title, rs, job_id)
            P("rank", "done", detail=f"Picked the best {len(hls)}")
        else:
            hls = hls[:opts["clips"]]
            P("rank", "skipped", detail="AI moment picking is off (AI page)" if not use_rank else "Few candidates")
        clip_ids = _make_clips(job_id, job, raw, hls, title, opts)
        P("clips", "done", detail=f"{len(clip_ids)} clip(s) queued for rendering")
        store.update(job_id, status="done")
        for cid in clip_ids:
            _dispatch("render_clip_task", cid)
        return clip_ids
    except Exception as e:
        log.exception("ingest failed")
        progress.fail_active(job_id, str(e))
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Job {job_id} failed while ingesting: {str(e)[:400]}\nRetry it from the web UI.")
        return []


def _most_replayed(job_id: str, job: dict, P) -> list[str] | None:
    """YouTube's "Most replayed" graph: download only its peaks (not the whole video) and make a clip from
    each. Returns the clip ids, or None when there is no graph (then the whole video is downloaded and
    analysed as before)."""
    s, store = get_settings(), get_store()
    opts = job_options(job)
    if not s.most_replayed or opts.get("most_replayed") is False:
        return None
    try:
        info = ingest.probe_url(job["source"])
    except Exception as e:
        log.info("no metadata for %s: %s", job["source"], e)
        return None
    if info.get("is_live"):
        return None  # download_url switches it to the live recorder
    duration = float(info.get("duration") or 0)
    clip_s = float(opts["clip_seconds"])
    if duration < max(180.0, clip_s * 4):
        return None  # short video: download it all and analyse it
    peaks = moments.heatmap_peaks(info.get("heatmap"), duration, clip_s, int(opts["clips"]))
    if not peaks:
        return None
    pad = 8.0
    ranges = [(max(0.0, a - pad), min(duration, b + pad)) for a, b, _ in peaks]
    got = sum(b - a for a, b in ranges)
    title = job["title"] or info.get("title")
    P("download", detail=f"Most replayed: downloading {len(ranges)} peak moments ({got / 60:.1f} min of "
                         f"{duration / 60:.0f} min)…")
    files = moments.download_sections(job["source"], ranges, s.raw_dir / job_id, on_progress=P)
    meta = {k: info.get(k) for k in ("id", "title", "uploader", "duration", "webpage_url", "extractor")}
    risk = rights.assess({k: info.get(k) for k in rights.RIGHTS_FIELDS}, "url")
    store.update(job_id, raw_path=str(files[0]), title=title,
                 meta={"source": meta, "rights": risk, "moment_source": "most_replayed",
                       "heatmap_peaks": [{"start": a, "end": b, "replayed": v} for a, b, v in peaks]})
    if risk["risk"] == "high":
        _notify(f"{rights.summary_line(risk)} for '{title}'.\n" + "\n".join(f"• {r}" for r in risk["reasons"][:3]))
    P("download", "done", detail=f"Downloaded only the {len(files)} most replayed parts ({got / 60:.1f} min)")
    store.update(job_id, status="analyzing")
    P("analyze", detail="Lining up each clip on its peak…")
    clip_ids: list[str] = []
    for n, (f, (a, b, v)) in enumerate(zip(files, peaks), 1):
        try:
            h = highlight_ai.find_highlights(f, s.temp_dir, target_s=clip_s, min_s=s.clip_min_s,
                                             max_s=s.clip_max_s, top_n=1)[0]
        except Exception as e:
            log.warning("peak %s unreadable: %s", f.name, e)
            continue
        h.score = max(h.score, v * 3)
        clip_ids += _make_clips(job_id, job, f, [h], title, opts,
                                name_prefix=title if len(files) == 1 else f"{title} (part {n})")
    P("analyze", "done", detail=f"{len(clip_ids)} moments from the Most replayed graph")
    P("rank", "skipped", detail="YouTube viewers already picked these (Most replayed)")
    P("clips", "done", detail=f"{len(clip_ids)} clip(s) queued for rendering")
    store.update(job_id, status="done")
    for cid in clip_ids:
        _dispatch("render_clip_task", cid)
    return clip_ids


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
                                          "tease": opts.get("tease", True),
                                          "replay": opts.get("replay", True),
                                          "beat": opts.get("beat", True),
                                          "campaign": opts.get("campaign"),
                                          "audio_score": round(float(h.score), 3)}))
    return ids


# ---------------------------------------------------------------- render
def post_package(title: str, meta: dict | None = None) -> dict:
    """Everything each platform gets: YouTube title/description/tags, Instagram/TikTok/Facebook captions."""
    meta = meta or {}
    md = meta.get("llm") or {}
    src = meta.get("source_info") or {}
    refs = (src.get("video_title") or "", src.get("uploader") or "")
    src_title = seo.fix_spelling(src.get("title") or title or "", *refs)
    title = seo.fix_spelling(title, src_title, *refs)
    md = clean_llm_text(md, src_title, *refs)
    transcript = meta.get("transcript") or ""
    rs = runtime_settings.load()
    default = rs.get("default_hashtags") or get_settings().default_hashtags
    extra = campaigns.caption_extras(meta.get("campaign"))
    camp_tags = " ".join(t for t in extra.split() if t.startswith("#"))
    mentions = " ".join(t for t in extra.split() if not t.startswith("#"))
    phrases = md.get("search_phrases") or [k.lower() for k in seo.keywords(src_title, transcript, 10)]
    custom = meta.get("caption_custom") or None
    hook = seo.fix_spelling(meta.get("hook_text") or "", src_title, *refs) or None
    keyword = seo.primary_keyword(src_title, src.get("uploader"), phrases)

    def tags(limit: int, platform: str = "youtube") -> list[str]:
        return seo.hashtags(llm_tags=md.get("hashtags"), source_title=src_title, transcript=transcript,
                            default_tags=default, campaign_tags=camp_tags, limit=limit, platform=platform)
    yt_tags = tags(3)  # YouTube shows the first 3 above the title; more than that reads as spam
    caption = md.get("caption")
    short = dict(title=title, hook=hook, caption=caption, search_phrases=phrases, campaign_extra=mentions,
                 custom=custom, summary=md.get("description"), keyword=keyword, reference=src_title)
    return {
        "title": title,
        "keyword": keyword,
        "youtube_category": seo.category_id(src_title, title, md.get("description") or "", " ".join(phrases),
                                            default=get_settings().youtube_category_id),
        "hashtags": yt_tags,
        "youtube_description": seo.youtube_description(
            title=title, hook=hook, caption=caption, summary=md.get("description"), search_phrases=phrases,
            tags=yt_tags, source_title=src_title, uploader=src.get("uploader"), source_url=src.get("url"),
            campaign_extra=mentions, custom=custom),
        "youtube_tags": seo.youtube_tags(title, src_title, phrases, yt_tags, src.get("uploader")),
        "instagram": seo.short_caption(**short, tags=tags(5), max_tags=5),
        "tiktok": seo.short_caption(**short, tags=tags(5, "tiktok"), max_tags=5),
        "facebook": seo.short_caption(**short, tags=tags(3), max_tags=3),
    }


def clean_llm_text(md: dict, source_title: str, *refs: str) -> dict:
    """Spell-check what the AI wrote against the real video title and creator ('Kai Centa' -> 'Kai Cenat',
    'doomday' -> 'doomsday'), and drop repeated search phrases."""
    out = dict(md or {})
    for k in ("title", "hook", "caption", "description"):
        if isinstance(out.get(k), str):
            out[k] = seo.fix_spelling(out[k], source_title, *refs)
    for k in ("search_phrases", "hashtags", "keywords"):
        if isinstance(out.get(k), list):
            vals = [seo.fix_spelling(str(v), source_title, *refs) for v in out[k]]
            out[k] = list(dict.fromkeys(vals))
    return out


def _title_key(t: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def unique_title(title: str, job_id: str, parent_id: str | None = None) -> str:
    """No two clips get the same title (YouTube treats copies as duplicates). Checks this video's other clips
    and everything published or scheduled; adds 'Part 2', 'Part 3'... when needed."""
    import re

    title = " ".join((title or "").replace("#Shorts", "").replace("#shorts", "").split())
    if not title:
        return title
    store = get_store()
    others = store.children(parent_id) if parent_id else []
    for st in ("published", "publishing", "scheduled", "awaiting_approval", "approved"):
        others += store.by_status(st)
    used = {_title_key(c["title"]) for c in others if c["id"] != job_id and c.get("title")}
    if _title_key(title) not in used:
        return title
    base = re.sub(r"\s*[|(-]?\s*part \d+\)?$", "", title, flags=re.I).strip()
    for n in range(2, 50):
        cand = f"{base} | Part {n}"[:100] if len(base) <= 88 else f"{base[:88]}… Part {n}"
        if _title_key(cand) not in used:
            return cand
    return title


def caption_text(title: str, meta: dict | None = None) -> str:
    """The full YouTube description (also shown in Telegram for review)."""
    return post_package(title, meta)["youtube_description"]


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


def make_cover(job_id: str, raw: Path, start: float, end: float, out_w: int, out_h: int, crop_x,
               text: str | None, payoff: float | None = None) -> tuple[Path, float]:
    """Cover image: the user's own picture, or their chosen moment, or the sharpest frame around the
    payoff (the reaction everyone wants to see), with the hook in big bold text."""
    s, store = get_settings(), get_store()
    meta = store.get(job_id)["meta"]
    dst = s.processed_dir / f"{job_id}.jpg"
    cover_ass = transcriber.write_ass([], s.temp_dir / f"{job_id}_cover.ass", out_w, out_h, font=s.caption_font,
                                      style="none", hook=text, hook_seconds=5, animate_hook=False, hook_scale=1.7)
    try:
        custom = meta.get("cover_image")
        if custom and Path(custom).exists():
            return video_editor.cover_from_image(Path(custom), dst, out_w, out_h, cover_ass), start
        t = meta.get("cover_time")
        if t is not None and 0 <= float(t) <= end - start:
            t = start + float(t)
        elif payoff is not None:
            t = video_editor.pick_cover_time(raw, start + max(0.0, payoff - 1.5), start + min(end - start, payoff + 0.7),
                                             samples=8)
        else:
            t = video_editor.pick_cover_time(raw, start, end)
        return video_editor.make_cover(raw, dst, t, out_w, out_h, crop_x, cover_ass), t
    finally:
        if cover_ass:
            cover_ass.unlink(missing_ok=True)


def timed_transcript(words: list, every: float = 4.0) -> str:
    """'[0s] so I jump [4s] and then...' so the LLM can say when the payoff happens."""
    out, mark = [], -every
    for w in words:
        if w.start - mark >= every:
            mark = w.start
            out.append(f"[{w.start:.0f}s]")
        out.append(w.text)
    return " ".join(out)


def remap_words_spans(words: list, spans: list) -> list:
    """Words for the final video: every span is played in order, a word inside a span shows there
    (so a line in the payoff is captioned in the tease, the body and the replay)."""
    out, acc = [], 0.0
    for sp in spans:
        a, b = sp[0], sp[1]
        speed = sp[2] if len(sp) > 2 and sp[2] else 1.0
        for w in words:
            if a <= w.start < b:
                out.append(transcriber.Word(acc + (w.start - a) / speed, acc + (min(w.end, b) - a) / speed, w.text))
        acc += (b - a) / speed
    return sorted(out, key=lambda w: w.start)


def plan_edit(words: list, duration: float, keep, payoff: float | None, tease: bool, replay: bool,
              max_total: float = 180.0) -> tuple[list, dict]:
    """Final span list: [payoff tease] + body (jump cuts) + [slow-mo replay]. Returns (spans, info)."""
    body = [(a, b, 1.0) for a, b in keep] if keep else [(0.0, duration, 1.0)]
    info = {"tease": None, "replay": None}
    body_len = video_editor.spans_duration(body)
    spans = list(body)
    if payoff is not None and replay and body_len + 4.5 <= max_total and duration >= 8:
        a, b = max(0.0, payoff - 2.0), min(duration, payoff + 1.0)
        if b - a >= 1.5:
            spans.append((a, b, 0.7))
            info["replay"] = (a, b)
    if payoff is not None and tease and payoff >= 4.0 and video_editor.spans_duration(spans) + 2.2 <= max_total:
        a, b = max(0.0, payoff - 1.2), min(duration, payoff + 0.8)
        if b - a >= 1.0:
            spans.insert(0, (a, b, 1.0))
            info["tease"] = (a, b)
    return spans, info


def render_clip(job_id: str) -> Path | None:
    s, store = get_settings(), get_store()
    if not store.transition(job_id, ("queued", "failed"), "rendering"):
        return None
    job = store.get(job_id)
    P = progress.Stepper(job_id)
    progress.start(job_id, progress.CLIP_STAGES)
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
        duration = end - start
        P("transcribe", detail=f"Listening to {duration:.0f} s of audio ({rs['whisper_model']} model)…")
        try:
            words = _transcript(raw, start, end, rs)
            P("transcribe", "done", detail=f"{len(words)} words heard" if words else "No clear speech (action-only clip)")
        except Exception as e:  # captions are optional; never lose the clip over them
            log.warning("transcription failed for %s: %s", job_id, e)
            store.update(job_id, meta={"caption_error": f"transcription failed: {str(e)[:250]}"})
            P("transcribe", "warn", detail=f"Failed, continuing without captions: {str(e)[:150]}")
        parent = store.get(job["parent_id"]) if job.get("parent_id") else None
        pmeta = (parent or {}).get("meta") or {}
        source_title = (parent or {}).get("title") or job["title"] or ""
        src = pmeta.get("source") or {}
        refs = (src.get("title") or "", src.get("uploader") or "", src.get("channel") or "")
        source_title = seo.fix_spelling(source_title, *refs)
        store.update(job_id, meta={"transcript": " ".join(w.text for w in words)[:3000],
                                   "source_info": {"title": source_title, "uploader": src.get("uploader"),
                                                   "video_title": src.get("title"),
                                                   "url": src.get("webpage_url") or (job["source"] if str(job.get("source") or "").startswith("http") else None)}})
        md: dict = dict(meta.get("llm") or {})
        if use_llm_meta and (not md or meta.get("llm_refresh")):
            P("ai", detail=f"{rs['llm_model']} is writing the hook, title, description and hashtags…")
            try:
                used = [x for c in (store.children(job["parent_id"]) if job.get("parent_id") else [])
                        if c["id"] != job_id for x in (c["meta"].get("hook_text"), c["title"])]
                md = clean_llm_text(llm.clip_metadata(timed_transcript(words), source_title, fmt, rs,
                                                      avoid=used[:12]), source_title, *refs)
                store.update(job_id, meta={"llm": md, "llm_refresh": False})
                P("ai", "done", detail=f"Title: {md.get('title') or '?'} · {len(md.get('hashtags') or [])} hashtags")
            except Exception as e:
                log.warning("LLM metadata skipped for %s: %s", job_id, e)
                store.update(job_id, meta={"llm_error": str(e)[:300]})
                P("ai", "warn", detail=f"AI failed ({str(e)[:120]}); using automatic SEO from the source title")
        elif md:
            P("ai", "done", detail="Kept the AI text from before")
        else:
            P("ai", "done", detail="AI is off: automatic SEO from the source title and transcript")
        if not meta.get("title_locked"):
            new_title = unique_title(md.get("title") or job["title"] or "", job_id, job.get("parent_id"))
            if new_title and new_title != job["title"]:
                store.update(job_id, title=new_title)
                job["title"] = new_title
        hook = None
        if meta.get("hook", s.hook_overlay):
            hook = meta.get("hook_custom") or md.get("hook") or fallback_hook(source_title)
            if hook and not meta.get("hook_custom"):
                hook = seo.fix_spelling(hook, source_title, *refs)

        P("plan", detail="Finding the payoff moment and dead air…")
        keep = None
        if want_cuts and len(words) >= 6:
            keep = video_editor.jump_cut_spans(words, duration,
                                               gap_is_dead=video_editor.dead_gap_checker(raw, start, duration))
        want_tease, want_replay = meta.get("tease", s.payoff_tease), meta.get("replay", s.payoff_replay)
        payoff, payoff_src = None, None
        llm_p = md.get("payoff_second")  # also used for the cover frame and a punch-in, so always found
        if llm_p is not None and 2.5 <= float(llm_p) <= duration - 0.5:
            payoff, payoff_src = float(llm_p), "AI"
        else:
            try:
                payoff, _strength = video_editor.payoff_time(raw, start, duration)
                payoff_src = "audio/motion peak"
            except Exception as e:
                log.warning("payoff detection failed for %s: %s", job_id, e)
        if keep and payoff is not None and video_editor.remap_time(payoff, keep) is None:
            keep = [(a, b) for a, b in keep] + [(max(0.0, payoff - 1.0), min(duration, payoff + 1.0))]
            keep = _merge_spans(keep)
        spans, info = plan_edit(words, duration, keep, payoff, want_tease, want_replay)
        out_words = remap_words_spans(words, spans)
        out_dur = video_editor.spans_duration(spans)
        tease_len = (info["tease"][1] - info["tease"][0]) if info["tease"] else 0.0
        labels, zooms, r_start = [], None, out_dur
        if info["replay"]:
            ra, rb = info["replay"]
            r_start = out_dur - (rb - ra) / 0.7
            labels.append((r_start, out_dur, "INSTANT REPLAY"))
        if meta.get("zoom", s.zoom_punch):
            body_words = [w for w in out_words if tease_len <= w.start < (r_start if info["replay"] else out_dur)]
            zooms = [z for z in zoom_times(body_words, set(md.get("keywords") or []), out_dur)
                     if z[0] >= tease_len and (not info["replay"] or z[1] <= r_start)]
            if info["tease"]:
                zooms.insert(0, (0.0, tease_len, 0.18))
            if info["replay"]:
                zooms.append((r_start + 0.3, out_dur, 0.3))
            # punch in on the payoff inside the body too
            if payoff is not None:
                for t in video_editor.remap_all(payoff, spans):
                    if tease_len + 0.2 < t < (r_start if info["replay"] else out_dur) - 0.2:
                        zooms.append((max(tease_len, t - 0.3), t + 1.0, 0.2))
            zooms.sort()
        ass = transcriber.write_ass(out_words, s.temp_dir / f"{job_id}.ass", out_w, out_h, s.caption_words_per_line,
                                    s.caption_font, style=style, hook=hook, hook_seconds=s.hook_seconds,
                                    keywords=set(md.get("keywords") or []),
                                    watermark=(rs.get("watermark") or "").strip() or None, labels=labels)
        plan_bits = []
        if payoff is not None:
            plan_bits.append(f"payoff at {payoff:.1f} s ({payoff_src})")
        if keep:
            plan_bits.append(f"{len(keep) - 1} jump cuts")
        if info["tease"]:
            plan_bits.append("2 s payoff tease at the start")
        if info["replay"]:
            plan_bits.append("slow-mo replay at the end (loops)")
        if zooms:
            plan_bits.append(f"{len(zooms)} zooms")
        P("plan", "done", detail=", ".join(plan_bits) or "Plain cut")

        out = s.processed_dir / f"{job_id}.mp4"
        info_src = video_editor.probe(raw)
        crop_x = None
        if info_src.aspect > (out_w / out_h) * 1.01:
            crop_x = video_editor.find_crop_x(raw, start, duration, info_src, target_aspect=out_w / out_h)
        # Cover first: it is also the clip's opening frames, so every platform shows it as the thumbnail.
        P("cover", detail="Picking the most expressive frame and adding bold text…")
        cover, cover_t = None, start
        try:
            cover, cover_t = make_cover(job_id, raw, start, end, out_w, out_h, crop_x,
                                        meta.get("cover_text") or hook, payoff=payoff)
            P("cover", "done", detail=f"Frame at {cover_t - start:.1f} s" + (" with hook text" if (meta.get("cover_text") or hook) else ""))
        except Exception as e:
            log.warning("cover failed for %s: %s", job_id, e)
            P("cover", "warn", detail=f"No cover: {str(e)[:150]}")
        intro = cover if (cover and meta.get("cover_intro", s.cover_intro)) else None
        track = None
        if meta.get("beat", s.beat):
            try:
                track = music.pick_track(s.data_dir, job_id)
            except Exception as e:
                log.warning("no background track: %s", e)
        P("render", detail=f"Encoding {out_dur:.0f} s at {out_w}x{out_h}" + (f" with beat '{track.stem}'" if track else ""), pct=0)
        video_editor.render_clip(raw, out, start, end, ass_file=ass, out_w=out_w, out_h=out_h, crop_x=crop_x,
                                 progress_bar=meta.get("progress_bar", s.progress_bar), keep=spans,
                                 zoom_times=zooms, intro_image=intro, music=track,
                                 music_volume=float(rs.get("beat_volume") or s.beat_volume),
                                 on_progress=lambda pct: P("render", pct=pct))
        P("render", "done", detail=f"{out.stat().st_size / 1e6:.1f} MB")
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
                           "seconds_removed": round(duration - sum(b - a for a, b in keep), 1) if keep else 0,
                           "zooms": len(zooms or []), "payoff_s": round(payoff, 1) if payoff is not None else None,
                           "tease": bool(info["tease"]), "replay": bool(info["replay"]),
                           "beat_track": track.name if track else None})
        P("finish", detail="Sending to Telegram for review…" if s.telegram_bot_token else "Saving…")
        store.transition(job_id, "rendering", "awaiting_approval")
        _send_for_review(job_id)
        P("finish", "done", detail="Ready for review")
        return out
    except Exception as e:
        log.exception("render failed")
        progress.fail_active(job_id, str(e))
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Clip {job_id} failed to render: {e}")
        return None


def _merge_spans(spans: list) -> list:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


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
def chosen_platforms(meta: dict, configured: dict[str, bool] | None = None) -> list[str]:
    """Where this clip will be posted: the platforms picked for it, else every connected one."""
    from app.services import social_publisher as sp

    configured = configured if configured is not None else sp.publishers_configured()
    picked = meta.get("platforms")
    keys = [k for k in sp.PLATFORM_KEYS if configured.get(k)]
    return [k for k in keys if k in picked] if picked else keys


def set_platforms(job_id: str, platforms: list[str] | None) -> list[str]:
    """platforms=None (or empty) resets to 'all connected platforms'."""
    from app.services import social_publisher as sp

    store = get_store()
    if not store.get(job_id):
        raise ValueError("unknown clip")
    bad = [p for p in platforms or [] if p not in sp.PLATFORM_KEYS]
    if bad:
        raise ValueError(f"unknown platform {', '.join(bad)}")
    store.update(job_id, meta={"platforms": list(dict.fromkeys(platforms)) if platforms else None})
    return chosen_platforms(store.get(job_id)["meta"])


def toggle_platform(job_id: str, platform: str) -> list[str]:
    """Telegram toggle: flip one platform on or off for this clip, keeping at least one."""
    store = get_store()
    job = store.get(job_id)
    if not job:
        raise ValueError("unknown clip")
    current = chosen_platforms(job["meta"])
    if platform in current:
        if len(current) == 1:
            raise ValueError("keep at least one platform")
        current.remove(platform)
    else:
        current.append(platform)
    return set_platforms(job_id, current)


def similar_recent(job_id: str, spacing_min: float | None = None) -> dict | None:
    """Another clip from the same video that went out (or is scheduled) less than spacing_min minutes ago or
    later. Returns {"id", "title", "at" (epoch s), "next_slot" (epoch s)} or None."""
    import time as _t
    from datetime import datetime

    spacing = float(get_settings().publish_spacing_min if spacing_min is None else spacing_min) * 60
    store = get_store()
    job = store.get(job_id)
    if not job or spacing <= 0:
        return None
    sibs = store.children(job["parent_id"]) if job.get("parent_id") else []
    key = _title_key(job.get("title") or "")
    sibs += [c for st in ("published", "publishing", "scheduled") for c in store.by_status(st)
             if key and _title_key(c.get("title") or "") == key]
    latest = None
    for c in sibs:
        if c["id"] == job_id or c["status"] not in ("published", "publishing", "scheduled"):
            continue
        m = c["meta"]
        at = m.get("posted_at")
        if c["status"] == "scheduled" and m.get("publish_at"):
            try:
                at = datetime.fromisoformat(m["publish_at"]).timestamp()
            except ValueError:
                at = None
        at = float(at or c.get("updated_at") or 0)
        if latest is None or at > latest[0]:
            latest = (at, c)
    if not latest or _t.time() - latest[0] >= spacing:
        return None
    at, c = latest
    return {"id": c["id"], "title": c["title"], "at": at, "next_slot": max(_t.time(), at + spacing)}


def approve(job_id: str, publish_at: str | None = None, platforms: list[str] | None = None,
            force: bool = False) -> str:
    """publish_at: optional ISO time (UTC or with offset). Until then the clip waits as 'scheduled'.
    platforms: optional subset of connected platforms; default is all of them.
    Without force, a clip whose sibling from the same video went out minutes ago is scheduled for the next
    free slot instead (PUBLISH_SPACING_MIN), so the channel doesn't post near-duplicates back to back."""
    from datetime import datetime, timezone

    from app.services import social_publisher as sp

    store = get_store()
    job = store.get(job_id)
    if not job:
        return "Unknown job"
    configured = sp.publishers_configured()
    if platforms is not None and any(configured.values()):
        if not [p for p in platforms if configured.get(p)]:
            raise ValueError("none of the chosen platforms is connected")
        set_platforms(job_id, platforms)
    if not any(configured.values()):
        if store.transition(job_id, "awaiting_approval", "approved"):
            return ("Approved and saved. Connect YouTube, Instagram or TikTok in the web UI (Publishing) "
                    "to post automatically; until then download it from the UI.")
        return f"Already {job['status']}"
    if not publish_at and not force and job["status"] in ("awaiting_approval", "approved"):
        near = similar_recent(job_id)
        if near:
            when = datetime.fromtimestamp(near["next_slot"], timezone.utc)
            if store.transition(job_id, ("awaiting_approval", "approved"), "scheduled"):
                store.update(job_id, meta={"publish_at": when.isoformat(), "spaced_after": near["id"]})
                mins = max(1, round((near["next_slot"] - datetime.now(timezone.utc).timestamp()) / 60))
                return (f"Scheduled in {mins} min ({when.isoformat(timespec='minutes')}): a similar clip from "
                        f"the same video (\"{near['title']}\") went out just before. Posting near-duplicates "
                        f"minutes apart looks like spam. Publish now anyway, or merge them into a compilation.")
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
        import time as _t

        store.update(job_id, meta={"posted_at": _t.time()})
        _dispatch("publish_clip_task", job_id)
        return "Approved, publishing now"
    job = store.get(job_id)
    return f"Already {job['status']}"


def make_compilation(clip_ids: list[str], title: str | None = None, background: bool = False) -> str:
    """Merge finished clips (best from one video) into one longer video, as a new clip waiting for approval.
    Better than posting near-identical Shorts minutes apart. The originals are left as they are."""
    store = get_store()
    clips = [store.get(i) for i in dict.fromkeys(clip_ids)]
    clips = [c for c in clips if c and c["kind"] == "clip" and c.get("output_path") and Path(c["output_path"]).exists()]
    if len(clips) < 2:
        raise ValueError("pick at least 2 finished clips")
    clips.sort(key=lambda c: (c.get("parent_id") or "", float(c.get("start_s") or 0)))
    first = clips[0]
    fm = first["meta"]
    fmt = fm.get("format", "vertical")
    out_w, out_h = video_editor.format_size(fmt)
    src = fm.get("source_info") or {}
    base = seo.fix_spelling(src.get("title") or first["title"] or "Clips", src.get("video_title") or "",
                            src.get("uploader") or "")
    name = title or f"{fallback_hook(base) or base} | Best Moments"
    job_id = store.create("clip", status="rendering", parent_id=first.get("parent_id"),
                          source_type=first.get("source_type"), source=first.get("source"),
                          raw_path=first.get("raw_path"), start_s=first.get("start_s"),
                          end_s=clips[-1].get("end_s"), score=max(float(c.get("score") or 0) for c in clips),
                          title=name[:100],
                          meta={"format": fmt, "compilation_of": [c["id"] for c in clips], "title_locked": bool(title),
                                "source_info": src, "llm": {k: v for k, v in (fm.get("llm") or {}).items()
                                                           if k in ("search_phrases", "hashtags", "keywords")},
                                "hook_text": None, "cover_path": fm.get("cover_path"), "campaign": fm.get("campaign")})
    if not title:
        store.update(job_id, title=unique_title(name, job_id, first.get("parent_id")))
    progress.start(job_id, [("render", "Join the clips into one video"), ("finish", "Send for review")])
    if background:
        import threading

        threading.Thread(target=_render_compilation, args=(job_id, clips, out_w, out_h), daemon=True).start()
    else:
        _render_compilation(job_id, clips, out_w, out_h)
    return job_id


def _render_compilation(job_id: str, clips: list[dict], out_w: int, out_h: int) -> None:
    from app.services.video_editor import concat_clips

    store = get_store()
    P = progress.Stepper(job_id)
    try:
        out = get_settings().processed_dir / f"{job_id}.mp4"
        P("render", detail=f"Joining {len(clips)} clips…", pct=0)
        concat_clips([Path(c["output_path"]) for c in clips], out, out_w, out_h, on_progress=lambda p: P("render", pct=p))
        P("render", "done", detail=f"{out.stat().st_size / 1e6:.1f} MB")
        dur = video_editor.probe(out).duration
        store.update(job_id, output_path=str(out), meta={"duration": round(dur, 1)})
        for c in clips:
            store.update(c["id"], meta={"merged_into": job_id})
        store.transition(job_id, "rendering", "awaiting_approval")
        P("finish", "done", detail=f"{dur:.0f} s compilation ready for review")
    except Exception as e:
        progress.fail_active(job_id, str(e))
        store.transition(job_id, "rendering", "failed", error=str(e)[:500])
        log.warning("compilation %s failed: %s", job_id, e)


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
            store.update(job["id"], meta={"posted_at": now.timestamp()})
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
    pkg = post_package(job["title"] or "", meta)
    configured = sp.publishers_configured()
    targets = chosen_platforms(meta, configured)
    todo = [t for t in targets if not meta.get(f"{t}_id")]  # idempotent: a retry skips what already succeeded
    F = "publish_progress"
    progress.start(job_id, progress.publish_stages(todo), field=F)
    store.update(job_id, meta={"post": pkg})
    results, errors, skipped, warnings = {}, [], [], []
    if not targets:
        errors.append("no publisher configured (connect YouTube, Instagram, Facebook or TikTok in Publishing)")
    cover = Path(meta["cover_path"]) if meta.get("cover_path") else None

    def stepper(platform: str):
        return progress.Stepper(job_id, field=F, prefix=f"{platform}.")

    def failed(platform: str, e: Exception) -> None:
        errors.append(f"{progress.PLATFORM_LABEL[platform]}: {e}")
        progress.fail_active(job_id, str(e), field=F)
        progress.skip_pending(job_id, field=F, reason="Stopped after the error above", prefix=f"{platform}.")

    if "youtube" in todo:
        P = stepper("youtube")
        try:
            is_short = meta.get("format", "vertical") != "landscape" and float(meta.get("duration") or 0) <= 180
            vid = sp.upload_youtube(video, pkg.get("title") or job["title"] or "Clip", pkg["youtube_description"],
                                    tags=pkg["youtube_tags"], shorts=is_short, thumbnail=cover, on_step=P,
                                    category_id=pkg.get("youtube_category"))
            results["youtube_id"], results["youtube_short"] = vid, is_short
            store.update(job_id, meta={"youtube_id": vid, "youtube_short": is_short})
            P("visibility", detail="Asking YouTube who can see it…")
            want = get_settings().youtube_privacy
            try:
                st = sp.youtube_status([vid]).get(vid, {})
                results["youtube_privacy"] = st.get("privacy")
                if st.get("privacy") == want:
                    P("visibility", "done", detail=f"{st.get('privacy', '?').title()}: anyone can find and watch it"
                      if want == "public" else f"{st.get('privacy')} (as set in YOUTUBE_PRIVACY)")
                else:
                    msg = f"YouTube shows it as {st.get('privacy') or 'unknown'}, not {want}."
                    if want == "public" and st.get("privacy") == "private":
                        msg += " " + sp.LOCKED_HELP
                    P("visibility", "warn", detail=msg)
                    warnings.append(f"YouTube: {msg}")
            except Exception as e:
                P("visibility", "warn", detail=f"Couldn't read it back ({str(e)[:120]}); check YouTube Studio")
            url = f"https://youtube.com/shorts/{vid}" if is_short else f"https://youtu.be/{vid}"
            P("link", "done", detail=url)
        except Exception as e:
            failed("youtube", e)
    if "instagram" in todo:
        P = stepper("instagram")
        try:
            results["instagram_id"] = sp.upload_instagram(video, pkg["instagram"],
                                                          thumb_offset_ms=meta.get("cover_offset_ms"), on_step=P)
            results["instagram_permalink"] = sp.instagram_permalink(results["instagram_id"])
            P("link", "done", detail=results["instagram_permalink"] or "Posted (link not available yet)")
        except Exception as e:
            failed("instagram", e)
    if "facebook" in todo:
        P = stepper("facebook")
        if float(meta.get("duration") or 0) > sp.FB_REEL_MAX_S:
            skipped.append(f"Facebook (Reels must be {sp.FB_REEL_MAX_S} s or shorter)")
            progress.skip_pending(job_id, field=F, reason=f"Reels must be {sp.FB_REEL_MAX_S} s or shorter",
                                  prefix="facebook.")
        else:
            try:
                results["facebook_id"] = sp.upload_facebook(video, pkg["facebook"], on_step=P)
            except Exception as e:
                failed("facebook", e)
    if "tiktok" in todo:
        P = stepper("tiktok")
        try:
            results["tiktok_id"] = sp.upload_tiktok(video, pkg["tiktok"], cover_ms=meta.get("cover_offset_ms") or 0,
                                                    on_step=P)
        except Exception as e:
            failed("tiktok", e)
    store.update(job_id, meta={**results, "publish_warnings": warnings})
    if not errors and skipped and not any(meta.get(f"{t}_id") or results.get(f"{t}_id") for t in targets):
        errors.extend(skipped)  # nothing was posted anywhere
    if errors:
        store.update(job_id, status="failed", error="; ".join(errors))
        _notify(f"⚠️ Clip {job_id} publish problems: {'; '.join(errors)}")
    else:
        store.update(job_id, status="published")
        links = campaigns.post_links({**meta, **results})
        camp = campaigns.get(meta.get("campaign"))
        msg = "✅ Published:\n" + "\n".join(f"{l['platform']}: {l['url']}" for l in links)
        if skipped:
            msg += "\n\nSkipped: " + "; ".join(skipped)
        if warnings:
            msg += "\n\n⚠️ " + "\n⚠️ ".join(warnings)
        if camp:
            msg += f"\n\n💰 Submit these links to {camp['name']}" + (f": {camp['url']}" if camp.get("url") else "")
        _notify(msg)
    return results


def make_youtube_public(job_ids: list[str] | None = None) -> list[dict]:
    """Switch published clips that are private/unlisted on YouTube to public (and report locked ones)."""
    from app.services import social_publisher as sp

    store = get_store()
    clips = [c for c in store.by_status("published") if c["meta"].get("youtube_id")
             and (job_ids is None or c["id"] in job_ids)]
    out = []
    for c in clips:
        vid = c["meta"]["youtube_id"]
        try:
            before = sp.youtube_status([vid]).get(vid, {})
            if before.get("privacy") == "public":
                st = before
            else:
                st = sp.set_youtube_privacy(vid, "public")
            ok = st.get("privacy") == "public"
            note = "public" if ok else f"still {st.get('privacy')}: " + sp.LOCKED_HELP
        except Exception as e:
            st, ok, note = {}, False, str(e)[:300]
        store.update(c["id"], meta={"youtube_privacy": st.get("privacy"), "youtube_public_note": None if ok else note})
        out.append({"id": c["id"], "youtube_id": vid, "title": c["title"], "privacy": st.get("privacy"),
                    "ok": ok, "note": note})
    return out


def refresh_stats() -> dict:
    """Read view counts of published clips and estimate campaign earnings."""
    from app.services import social_publisher as sp

    store = get_store()
    clips = store.by_status("published")
    yt_ids = [c["meta"]["youtube_id"] for c in clips if c["meta"].get("youtube_id")]
    errors = []
    yt, yt_status = {}, {}
    try:
        yt_status = sp.youtube_status(yt_ids)
        yt = {k: v["views"] for k, v in yt_status.items()}
    except Exception:
        try:
            yt = sp.youtube_views(yt_ids)
        except Exception as e:
            errors.append(f"YouTube views: {str(e)[:150]} (reconnect YouTube to allow reading views)")
    for c in clips:
        st = yt_status.get(c["meta"].get("youtube_id"))
        if st:
            store.update(c["id"], meta={"youtube_privacy": st["privacy"]})
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
            "not_public": sum(1 for v in yt_status.values() if v.get("privacy") != "public"),
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
LIVE_STAGES = [
    ("connect", "Connect to the stream"),
    ("watch", "Watch chat & audio for spikes (only the last few minutes are kept)"),
    ("clips", "Save the moments as clips"),
]


def record_live(job_id: str, realtime_input: bool = False) -> list[str]:
    """Watch a live stream without recording all of it.

    A rolling buffer keeps only the last LIVE_BUFFER_S seconds. Live chat (YouTube/Twitch) is watched for
    spikes (sudden speed-ups, floods of "LMAO"/"💀"/"clip it"); the buffer window around each spike becomes a
    clip and the rest is overwritten. Without readable chat, a segment much louder/busier than the recent
    baseline is the spike. A stream that has already ended goes to the normal link path (Most replayed)."""
    s, store = get_settings(), get_store()
    job = store.get(job_id)
    out_dir = s.live_dir / job_id
    opts = job_options(job)
    clip_ids: list[str] = []
    clip_s = float(opts["clip_seconds"])
    P = progress.Stepper(job_id)
    progress.start(job_id, LIVE_STAGES)
    buf = chat = None
    try:
        store.update(job_id, status="recording")
        P("connect", detail="Looking up the stream…")
        info: dict = {}
        src = job["source"]
        if str(src).startswith("http"):
            try:
                info = ingest.probe_url(src)
                store.update(job_id, title=job["title"] or info.get("title"),
                             meta={"rights": rights.check_url(src)})
                job["title"] = job["title"] or info.get("title")
            except Exception as e:
                log.info("stream metadata skipped for %s: %s", job_id, e)
            if info.get("live_status") in ("was_live", "not_live", "post_live"):
                P("connect", "done", detail="The stream has ended: clipping the recording from its Most replayed parts")
                store.update(job_id, source_type="url", status="queued")
                _dispatch("ingest_source_task", job_id)
                return []
        urls, headers = ingest.resolve_streams(src)
        buf = moments.RollingBuffer(urls, headers, out_dir, segment_s=min(int(s.live_segment_s), 20),  # old .env: 120 keep_s=s.live_buffer_s,
                                    max_s=s.live_max_hours * 3600, realtime_input=realtime_input)
        if s.live_chat and opts.get("live_chat", True):
            chat = moments.chat_for(str(src), info, out_dir / "chat")
            if chat:
                chat.start()
        P("connect", "done", detail=f"Connected. Keeping only the last {s.live_buffer_s // 60} min"
                                    + (f", watching {chat.name}" if chat else ", no chat for this site: using audio"))
        det = moments.SpikeDetector(ratio=s.live_chat_ratio)
        pending: list[tuple[float, float, str, float]] = []  # (from, to, reason, score) waiting for the post-roll
        history: list[float] = []
        t_start, last_note, n_spikes = time.time(), 0.0, 0

        def stop_requested() -> bool:
            return bool((store.get(job_id) or {}).get("meta", {}).get("stop_requested"))

        def chat_usable() -> bool:
            if not chat or chat.error:
                return False
            return bool(chat.messages) or time.time() - t_start < 120  # give it 2 min to show messages

        def save(a: float, b: float, reason: str, score: float) -> None:
            nonlocal n_spikes
            n_spikes += 1
            got = buf.save_window(a, b, out_dir / f"moment_{n_spikes:03d}.mp4")
            if not got:
                return
            f, ws, we = got
            try:
                h = highlight_ai.find_highlights(f, s.temp_dir, target_s=clip_s, min_s=s.clip_min_s,
                                                 max_s=s.clip_max_s, top_n=1)[0]
            except Exception as e:
                log.warning("moment %s unreadable: %s", f.name, e)
                return
            h.score = max(h.score, score)
            new = _make_clips(job_id, job, f, [h], job["title"], opts,
                              name_prefix=f"{job['title'] or 'Live highlight'} #{len(clip_ids) // len(opts['formats']) + 1}")
            t0 = buf.first_wall
            for cid in new:
                store.update(cid, meta={"live_reason": reason, "stream_offset_s": round(max(0.0, a - t0), 1)})
            clip_ids.extend(new)
            for cid in new:
                _dispatch("render_clip_task", cid)
            P("clips", detail=f"{len(clip_ids)} clip(s) so far · last: {reason}")
            log.info("live %s moment %s (%s) at %.0f s into the stream", job_id, f.name, reason, a - t0)

        P("watch", detail="Warming up: learning what normal chat speed looks like…")
        while True:
            for seg in buf.poll():
                if chat_usable():
                    continue
                try:  # fallback signal: this segment much louder/busier than the recent ones
                    score = highlight_ai.peak_score(seg["file"], s.temp_dir)
                except Exception as e:
                    log.warning("segment %s unreadable: %s", seg["file"].name, e)
                    continue
                baseline = statistics.median(history[-10:]) if len(history) >= 3 else None
                history.append(score)
                if baseline is not None and score > s.live_highlight_ratio * baseline:
                    mid = (seg["wall_start"] + seg["wall_end"]) / 2
                    if not pending or mid - pending[-1][1] > 5:
                        pending.append((mid - clip_s / 2 - 6, mid + clip_s / 2 + 6,
                                        f"audio/action {score / baseline:.1f}x louder", score / baseline))
            if chat_usable() and chat.messages:
                sp = det.check(chat.messages)
                if sp:  # chat reacts a few seconds after the moment, so most of the window is before it
                    pending.append((sp.at - clip_s * 0.75 - 8, sp.at + clip_s * 0.25 + 6, sp.reason, sp.score))
            ready = [p for p in pending if buf.latest_end >= p[1] or not buf.running]
            for p in ready:
                pending.remove(p)
                save(*p)
            now = time.time()
            if now - last_note > 5:
                last_note = now
                sig = (f"{chat.name}: {det.last_rate * 60:.0f} msgs/min (normal {det.baseline * 60:.0f})"
                       if chat_usable() and chat.messages else
                       ("audio/action levels" + (f" ({chat.name} unavailable)" if chat else "")))
                P("watch", detail=f"{(now - t_start) / 60:.0f} min watched · {sig} · {len(clip_ids)} clip(s) · "
                                  f"only the last {s.live_buffer_s // 60} min on disk")
            if not buf.running:
                if buf.count == 0:
                    raise RuntimeError("the stream recorder stopped before any video arrived: "
                                       + (buf.error() or "no details"))
                break
            if stop_requested():
                buf.stop()
                for p in pending:
                    save(*p)
                break
            time.sleep(1.0 if not realtime_input else 0.5)
        P("watch", "done", detail=f"Watched {(time.time() - t_start) / 60:.0f} min, {buf.total_s / 60:.0f} min of video seen")
        P("clips", "done", detail=f"{len(clip_ids)} clip(s) from the spikes")
        store.update(job_id, status="done", meta={"segments": buf.count, "clips": len(clip_ids),
                                                  "moment_source": "live chat" if chat and chat.messages else "audio"})
    except Exception as e:
        log.exception("live recording failed")
        progress.fail_active(job_id, str(e))
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Live job {job_id} stopped: {e}")
    finally:
        if chat:
            chat.stop()
        if buf:
            buf.stop()
            buf.cleanup()  # the buffer is thrown away; only the saved moments stay
    return clip_ids
