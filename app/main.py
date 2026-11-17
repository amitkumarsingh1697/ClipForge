"""FastAPI application and webhook endpoints.

FIXES vs the spec:
  * Every ingest endpoint requires X-API-Token (the spec exposed an open
    upload on a public static IP: anyone could fill the disk or run jobs).
    Prefer a private network like Tailscale (free) between phone and server.
  * Uploads are streamed to disk in chunks with a size cap and a type check
    instead of being read into memory.
  * Added URL and live-stream ingestion, job status, and approve/reject
    endpoints (usable without Telegram).
"""
from __future__ import annotations

import hmac
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, HttpUrl

from app import pipeline
from app.config import get_settings
from app.services import ingest, telegram_bot
from app.utils.job_store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Clip Agent")
ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def require_token(x_api_token: str = Header(default="")) -> None:
    expected = get_settings().api_token
    if not expected:
        raise HTTPException(503, "API_TOKEN is not configured on the server")
    if not hmac.compare_digest(x_api_token, expected):
        raise HTTPException(401, "bad or missing X-API-Token")


class UrlIn(BaseModel):
    url: HttpUrl
    title: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload", dependencies=[Depends(require_token)])
async def upload(file: UploadFile = File(...)):
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"unsupported file type {ext!r}")
    dst = s.raw_dir / f"{uuid.uuid4().hex[:8]}_{ingest.safe_name(Path(file.filename).stem)}{ext}"
    limit, written = s.max_upload_mb * 1024 * 1024, 0
    with dst.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                out.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(413, f"file exceeds {s.max_upload_mb} MB")
            out.write(chunk)
    job_id = pipeline.submit("upload", file.filename, raw_path=dst, title=Path(file.filename).stem)
    return {"status": "processing", "job_id": job_id}


@app.post("/ingest/url", dependencies=[Depends(require_token)])
def ingest_url(body: UrlIn):
    return {"status": "processing", "job_id": pipeline.submit("url", str(body.url), title=body.title)}


@app.post("/ingest/live", dependencies=[Depends(require_token)])
def ingest_live(body: UrlIn):
    return {"status": "recording", "job_id": pipeline.submit("live", str(body.url), title=body.title)}


@app.post("/ingest/live/{job_id}/stop", dependencies=[Depends(require_token)])
def stop_live(job_id: str):
    store = get_store()
    if not store.get(job_id):
        raise HTTPException(404)
    store.update(job_id, meta={"stop_requested": True})
    return {"status": "stopping"}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def job_status(job_id: str):
    store = get_store()
    job = store.get(job_id)
    if not job:
        raise HTTPException(404)
    return {**job, "clips": store.children(job_id)}


@app.post("/jobs/{job_id}/approve", dependencies=[Depends(require_token)])
def approve(job_id: str):
    return {"result": pipeline.approve(job_id)}


@app.post("/jobs/{job_id}/reject", dependencies=[Depends(require_token)])
def reject(job_id: str):
    return {"result": pipeline.reject(job_id)}


@app.post("/jobs/{job_id}/retry", dependencies=[Depends(require_token)])
def retry(job_id: str):
    """Re-run a failed clip: re-render if it never rendered, otherwise re-publish missing platforms."""
    store = get_store()
    job = store.get(job_id)
    if not job or job["status"] != "failed":
        raise HTTPException(409, "only failed jobs can be retried")
    if job["kind"] == "clip" and job.get("output_path") and Path(job["output_path"]).exists():
        store.transition(job_id, "failed", "publishing", error=None)
        pipeline._dispatch("publish_clip_task", job_id)
    elif job["kind"] == "clip":
        pipeline._dispatch("render_clip_task", job_id)
    else:
        store.transition(job_id, "failed", "queued", error=None)
        pipeline._dispatch("record_live_task" if job["source_type"] == "live" else "ingest_source_task", job_id)
    return {"status": "retrying"}


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    secret = get_settings().telegram_webhook_secret
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token, secret):
        raise HTTPException(401)
    update = await request.json()
    result = telegram_bot.handle_update(update, pipeline.approve, pipeline.reject, pipeline.submit_link)
    return {"ok": True, "result": result}


@app.get("/auth/youtube", dependencies=[Depends(require_token)])
def auth_youtube():
    """Status only; see app/utils/auth_helpers.py for why the browser flow runs via scripts/youtube_auth.py."""
    from app.services.social_publisher import publishers_configured

    return {"youtube_authorised": publishers_configured()["youtube"],
            "how": "run `docker compose run --rm -p 8765:8765 api python scripts/youtube_auth.py` "
                   "and open the printed URL through an SSH tunnel to localhost:8765"}


@app.get("/auth/youtube/callback")
def auth_youtube_callback():
    raise HTTPException(410, "Not used: Google rejects bare-IP http redirect URIs. Use scripts/youtube_auth.py.")
