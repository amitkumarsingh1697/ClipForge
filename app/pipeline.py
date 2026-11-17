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
from app.services import highlight_ai, ingest, telegram_bot, transcriber, video_editor
from app.utils.job_store import get_store

log = logging.getLogger(__name__)


def _dispatch(task_name: str, job_id: str) -> None:
    from app import celery_worker

    getattr(celery_worker, task_name).delay(job_id)


def _notify(text: str) -> None:
    s = get_settings()
    if s.telegram_bot_token and s.telegram_owner_chat_id:
        try:
            telegram_bot.send_text(text)
        except Exception as e:  # notification failure must not fail the job
            log.warning("telegram notify failed: %s", e)


# ---------------------------------------------------------------- creation
def submit(source_type: str, source: str, raw_path: Path | None = None, title: str | None = None) -> str:
    store = get_store()
    task = "record_live_task" if source_type == "live" else "ingest_source_task"
    job_id = store.create("source", source_type=source_type, source=source,
                          raw_path=str(raw_path) if raw_path else None, title=title)
    _dispatch(task, job_id)
    return job_id


def submit_link(url: str) -> str:
    """Used by the Telegram bot: a link that is live goes to the recorder, anything else is downloaded."""
    job_id = submit("url", url)
    return f"Queued {url} as job {job_id}. I'll send clips here for approval."


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
        hls = highlight_ai.find_highlights(raw, s.temp_dir, target_s=s.clip_target_s, min_s=s.clip_min_s,
                                           max_s=s.clip_max_s, top_n=s.clips_per_source)
        clip_ids = []
        base_title = title or Path(job["source"] or raw.name).stem
        for n, h in enumerate(hls, 1):
            t = base_title if len(hls) == 1 else f"{base_title} (part {n})"
            cid = store.create("clip", parent_id=job_id, source_type=job["source_type"], source=job["source"],
                               raw_path=str(raw), start_s=h.start, end_s=h.end, score=h.score, title=t)
            clip_ids.append(cid)
        store.update(job_id, status="done")
        for cid in clip_ids:
            _dispatch("render_clip_task", cid)
        return clip_ids
    except Exception as e:
        log.exception("ingest failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Job {job_id} failed while ingesting: {e}")
        return []


# ---------------------------------------------------------------- render
def caption_text(title: str) -> str:
    return f"{title}\n\n{get_settings().default_hashtags}".strip()


def render_clip(job_id: str) -> Path | None:
    s, store = get_settings(), get_store()
    if not store.transition(job_id, ("queued", "failed"), "rendering"):
        return None
    job = store.get(job_id)
    try:
        raw = Path(job["raw_path"])
        start, end = float(job["start_s"]), float(job["end_s"])
        ass = None
        n_words = 0
        if s.captions_enabled:
            try:
                words = transcriber.transcribe_words(raw, s.temp_dir, start, end - start, model=s.whisper_model,
                                                     device=s.whisper_device, compute_type=s.whisper_compute_type,
                                                     language=s.whisper_language)
                n_words = len(words)
                ass = transcriber.write_ass(words, s.temp_dir / f"{job_id}.ass", s.output_width, s.output_height,
                                            s.caption_words_per_line, s.caption_font)
            except Exception as e:  # captions are optional; never lose the clip over them
                log.warning("captions skipped for %s: %s", job_id, e)
                store.update(job_id, meta={"caption_error": str(e)[:300]})
        out = s.processed_dir / f"{job_id}.mp4"
        video_editor.render_vertical(raw, out, start, end, ass_file=ass, out_w=s.output_width,
                                     out_h=s.output_height, max_mb=s.max_output_mb)
        if ass:
            ass.unlink(missing_ok=True)
        store.update(job_id, output_path=str(out), meta={"caption_words": n_words})
        store.transition(job_id, "rendering", "awaiting_approval")
        if s.telegram_bot_token and s.telegram_owner_chat_id:
            mid = telegram_bot.send_for_approval(
                job_id, out, f"{job['title']}\n{end-start:.0f}s clip from {job['source'] or raw.name}")
            store.update(job_id, meta={"telegram_message_id": mid})
        return out
    except Exception as e:
        log.exception("render failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Clip {job_id} failed to render: {e}")
        return None


# ---------------------------------------------------------------- approval
def approve(job_id: str) -> str:
    store = get_store()
    if store.transition(job_id, "awaiting_approval", "publishing"):
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
    caption = caption_text(job["title"] or "")
    configured = sp.publishers_configured()
    results, errors = {}, []
    if not any(configured.values()):
        errors.append("no publisher configured (authorise YouTube and/or set Instagram credentials)")
    # Idempotent: a retry skips platforms that already succeeded.
    if configured["youtube"] and not meta.get("youtube_id"):
        try:
            results["youtube_id"] = sp.upload_youtube(video, job["title"] or "Clip", caption,
                                                      tags=["gaming", "shorts"])
        except Exception as e:
            errors.append(f"YouTube: {e}")
    if configured["instagram"] and not meta.get("instagram_id"):
        try:
            results["instagram_id"] = sp.upload_instagram(video, caption)
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
            links.append(f"https://youtube.com/shorts/{results['youtube_id']}")
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
            h = highlight_ai.find_highlights(seg, s.temp_dir, s.clip_target_s, s.clip_min_s, s.clip_max_s, top_n=1)[0]
            cid = store.create("clip", parent_id=job_id, source_type="live", source=job["source"],
                               raw_path=str(seg), start_s=h.start, end_s=h.end, score=score,
                               title=f"{job['title'] or 'Live highlight'} #{len(clip_ids) + 1}")
            clip_ids.append(cid)
            _dispatch("render_clip_task", cid)
        store.update(job_id, status="done", meta={"segments": len(history), "clips": len(clip_ids)})
    except Exception as e:
        log.exception("live recording failed")
        store.fail(job_id, f"{e}\n{traceback.format_exc()}")
        _notify(f"❌ Live job {job_id} stopped: {e}")
    return clip_ids
