"""Step-by-step progress for long jobs, stored in the job's meta so the UI can show it.

meta[<field>] = {"stages": [{"key", "label", "state", "detail", "pct", "t0", "t1"}], "current": key}

state is one of pending | active | done | skipped | failed | warn.
Field "progress" is used while a video is made (source jobs and clip jobs),
"publish_progress" while a clip is published.
"""
from __future__ import annotations

import threading
import time

from app.utils.job_store import get_store

SOURCE_STAGES = [
    ("download", "Download video"),
    ("analyze", "Find exciting moments (audio + motion)"),
    ("rank", "AI picks the best moments"),
    ("clips", "Queue clips"),
]
CLIP_STAGES = [
    ("transcribe", "Transcribe speech (Whisper)"),
    ("ai", "Write title, hook, description & hashtags"),
    ("plan", "Find the payoff moment, plan cuts, zooms & replay"),
    ("cover", "Make thumbnail cover"),
    ("render", "Render video (crop, captions, zooms, beat)"),
    ("finish", "Finish & send for review"),
]
PLATFORM_LABEL = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok"}
PUBLISH_STEPS = {
    "youtube": [("upload", "Upload video"), ("meta", "Title, description, hashtags & tags"),
                ("thumb", "Thumbnail"), ("visibility", "Check visibility (public?)"), ("link", "Live link")],
    "instagram": [("upload", "Upload video"), ("process", "Instagram processing"), ("publish", "Publish Reel"),
                  ("link", "Live link")],
    "facebook": [("upload", "Upload video"), ("publish", "Publish Reel"), ("process", "Facebook processing")],
    "tiktok": [("upload", "Upload video"), ("process", "TikTok processing")],
}

_lock = threading.Lock()
_last_write: dict[tuple[str, str, str], float] = {}


def _get(job_id: str, field: str) -> dict:
    job = get_store().get(job_id) or {}
    return dict((job.get("meta") or {}).get(field) or {"stages": []})


def start(job_id: str, stages: list[tuple[str, str]], field: str = "progress") -> None:
    get_store().update(job_id, meta={field: {
        "stages": [{"key": k, "label": label, "state": "pending", "detail": None, "pct": None} for k, label in stages],
        "current": None, "started": time.time()}})


def publish_stages(platforms: list[str]) -> list[tuple[str, str]]:
    out = []
    for p in platforms:
        for k, label in PUBLISH_STEPS.get(p, []):
            out.append((f"{p}.{k}", f"{PLATFORM_LABEL.get(p, p)}: {label}"))
    return out


def step(job_id: str, key: str, state: str = "active", detail: str | None = None, pct: float | None = None,
         field: str = "progress", throttle_s: float = 0.0) -> None:
    """Update one stage. Starting a stage marks earlier still-active stages done."""
    now = time.time()
    if throttle_s and state == "active":
        last = _last_write.get((job_id, field, key), 0.0)
        if now - last < throttle_s:
            return
    _last_write[(job_id, field, key)] = now
    with _lock:
        p = _get(job_id, field)
        stages = p.get("stages") or []
        found = None
        for s in stages:
            if s["key"] == key:
                found = s
        if found is None:
            found = {"key": key, "label": key, "state": "pending", "detail": None, "pct": None}
            stages.append(found)
        if state == "active":
            prefix = key.split(".")[0] + "." if "." in key else ""
            for s in stages:
                if s is found:
                    break
                if s["state"] == "active" and s["key"].startswith(prefix):
                    s["state"], s["t1"] = "done", now
        if found["state"] in ("pending", "done", "failed", "skipped", "warn") and state == "active":
            found["t0"] = now
        found["state"] = state
        if detail is not None:
            found["detail"] = detail[:300]
        if pct is not None:
            found["pct"] = round(max(0.0, min(100.0, float(pct))), 1)
        if state in ("done", "skipped", "failed", "warn"):
            found["t1"] = now
            if state == "done" and found.get("pct") is not None:
                found["pct"] = 100.0
        p["stages"], p["current"] = stages, key if state == "active" else p.get("current")
        get_store().update(job_id, meta={field: p})


def fail_active(job_id: str, error: str, field: str = "progress") -> None:
    """On an exception: mark whatever was running as failed, with the reason."""
    p = _get(job_id, field)
    for s in p.get("stages") or []:
        if s["state"] == "active":
            step(job_id, s["key"], "failed", detail=error[:300], field=field)
            return


def skip_pending(job_id: str, field: str = "progress", reason: str | None = None, prefix: str = "") -> None:
    p = _get(job_id, field)
    for s in p.get("stages") or []:
        if s["state"] == "pending" and s["key"].startswith(prefix):
            step(job_id, s["key"], "skipped", detail=reason, field=field)


class Stepper:
    """Callback object handed to publishers/renderers: on(key, state, detail, pct)."""

    def __init__(self, job_id: str, field: str = "progress", prefix: str = ""):
        self.job_id, self.field, self.prefix = job_id, field, prefix

    def __call__(self, key: str, state: str = "active", detail: str | None = None, pct: float | None = None) -> None:
        try:
            step(self.job_id, self.prefix + key, state, detail, pct, field=self.field,
                 throttle_s=1.0 if pct is not None and state == "active" else 0.0)
        except Exception:  # progress must never break the real work
            pass
