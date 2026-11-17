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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, HttpUrl

from app import pipeline
from app.config import get_settings
from app.services import ingest, telegram_bot
from app.utils.job_store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Clip Agent")
ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
UI_FILE = Path(__file__).parent / "static" / "index.html"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_token(request: Request, x_api_token: str = Header(default=""),
                  token: str = Query(default="")) -> None:
    """X-API-Token header (or ?token= for <video> tags). With no API_TOKEN set,
    only requests from this machine are allowed (local Mac use)."""
    expected = get_settings().api_token
    given = x_api_token or token
    if not expected:
        if request.client and request.client.host in LOCAL_HOSTS:
            return
        raise HTTPException(503, "API_TOKEN is not configured on the server")
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "bad or missing X-API-Token")


class UrlIn(BaseModel):
    url: HttpUrl
    title: str | None = None
    formats: list[str] | str | None = None   # vertical, portrait, square, landscape
    clips: int | None = None
    clip_seconds: float | None = None
    captions: bool | None = None
    caption_style: str | None = None
    hook: bool | None = None
    progress_bar: bool | None = None

    def options(self) -> dict:
        try:
            return pipeline.clean_options(self.formats, self.clips, self.clip_seconds, self.captions,
                                          self.caption_style, self.hook, self.progress_bar)
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui():
    return UI_FILE.read_text(encoding="utf-8")


@app.get("/api/config", dependencies=[Depends(require_token)])
def api_config():
    from app.services.social_publisher import publishers_configured
    from app.services.video_editor import FORMATS

    s = get_settings()
    return {"formats": {k: f"{w}x{h}" for k, (w, h) in FORMATS.items()},
            "default_formats": [f.strip() for f in s.output_formats.split(",") if f.strip()],
            "clips": s.clips_per_source, "clip_seconds": s.clip_target_s,
            "clip_min_s": s.clip_min_s, "clip_max_s": s.clip_max_s, "captions": s.captions_enabled,
            "caption_style": s.caption_style, "hook": s.hook_overlay, "progress_bar": s.progress_bar,
            "telegram": bool(s.telegram_bot_token and s.telegram_owner_chat_id),
            **publishers_configured()}


class SettingsIn(BaseModel):
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_titles: bool | None = None
    llm_rank: bool | None = None
    whisper_model: str | None = None


@app.get("/api/settings", dependencies=[Depends(require_token)])
def get_runtime_settings():
    from app.utils import runtime_settings

    return runtime_settings.public(runtime_settings.load())


@app.put("/api/settings", dependencies=[Depends(require_token)])
def put_runtime_settings(body: SettingsIn):
    from app.utils import runtime_settings

    changes = body.model_dump(exclude_none=True)
    try:
        return runtime_settings.public(runtime_settings.save(changes))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/llm/models", dependencies=[Depends(require_token)])
def llm_models(provider: str, base_url: str):
    from app.services import llm
    from app.utils import runtime_settings

    try:
        return {"models": llm.list_models(provider, base_url, runtime_settings.load()["llm_api_key"])}
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


@app.post("/api/llm/test", dependencies=[Depends(require_token)])
def llm_test():
    from app.services import llm

    try:
        md = llm.clip_metadata("no way, did you see that jump? that was insane", "Test video", "vertical")
        return {"ok": True, "sample": md}
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/api/jobs", dependencies=[Depends(require_token)])
def api_jobs(limit: int = 30):
    store = get_store()
    return [{**j, "clips": store.children(j["id"])} for j in store.list_sources(limit)]


@app.get("/media/{job_id}.jpg", dependencies=[Depends(require_token)])
def cover(job_id: str):
    job = get_store().get(job_id)
    path = (job or {}).get("meta", {}).get("cover_path")
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg",
                        filename=f"{ingest.safe_name(job['title'] or job_id)}_cover.jpg")


@app.get("/media/{job_id}.mp4", dependencies=[Depends(require_token)])
def media(job_id: str):
    job = get_store().get(job_id)
    if not job or not job.get("output_path") or not Path(job["output_path"]).exists():
        raise HTTPException(404)
    return FileResponse(job["output_path"], media_type="video/mp4",
                        filename=f"{ingest.safe_name(job['title'] or job_id)}_{job['meta'].get('format', '')}.mp4")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload", dependencies=[Depends(require_token)])
async def upload(file: UploadFile = File(...), formats: str | None = Form(None), clips: int | None = Form(None),
                 clip_seconds: float | None = Form(None), captions: bool | None = Form(None),
                 title: str | None = Form(None), caption_style: str | None = Form(None),
                 hook: bool | None = Form(None), progress_bar: bool | None = Form(None)):
    s = get_settings()
    try:
        opts = pipeline.clean_options(formats, clips, clip_seconds, captions, caption_style, hook, progress_bar)
    except ValueError as e:
        raise HTTPException(422, str(e))
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
    job_id = pipeline.submit("upload", file.filename, raw_path=dst, title=title or Path(file.filename).stem,
                             options=opts)
    return {"status": "processing", "job_id": job_id}


@app.post("/ingest/url", dependencies=[Depends(require_token)])
def ingest_url(body: UrlIn):
    return {"status": "processing",
            "job_id": pipeline.submit("url", str(body.url), title=body.title, options=body.options())}


@app.post("/ingest/live", dependencies=[Depends(require_token)])
def ingest_live(body: UrlIn):
    return {"status": "recording",
            "job_id": pipeline.submit("live", str(body.url), title=body.title, options=body.options())}


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


class RerenderIn(BaseModel):
    caption_style: str | None = None
    hook: bool | None = None
    progress_bar: bool | None = None


@app.post("/jobs/{job_id}/rerender", dependencies=[Depends(require_token)])
def rerender(job_id: str, body: RerenderIn | None = None):
    """Make a clip again (e.g. after a fix, or with another caption style). Same moment, same format."""
    store = get_store()
    job = store.get(job_id)
    if not job or job["kind"] != "clip":
        raise HTTPException(404)
    if job["status"] in ("rendering", "publishing", "published"):
        raise HTTPException(409, f"clip is {job['status']}")
    changes = {}
    if body:
        try:
            changes = pipeline.clean_options(caption_style=body.caption_style, hook=body.hook,
                                             progress_bar=body.progress_bar)
        except ValueError as e:
            raise HTTPException(422, str(e))
    store.update(job_id, status="queued", error=None, meta=changes)
    pipeline._dispatch("render_clip_task", job_id)
    return {"status": "rendering"}


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


@app.get("/api/publishing", dependencies=[Depends(require_token)])
def publishing_status():
    from app.services.social_publisher import publishers_configured
    from app.utils.auth_helpers import instagram_user_id, youtube_client_secrets_path

    st = publishers_configured()
    return {**st, "youtube_client_uploaded": youtube_client_secrets_path().exists(),
            "instagram_user_id": instagram_user_id(), "youtube_privacy": get_settings().youtube_privacy}


@app.post("/api/publishing/youtube/client", dependencies=[Depends(require_token)])
async def youtube_client(file: UploadFile = File(...)):
    import json

    from app.utils.auth_helpers import youtube_client_secrets_path

    raw = await file.read(200_000)
    try:
        data = json.loads(raw)
        assert "installed" in data or "web" in data
    except Exception:
        raise HTTPException(422, "That isn't a Google OAuth client JSON (expected an 'installed' or 'web' client)")
    path = get_settings().secrets_dir / "client_secret.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    return {"ok": True, "type": "desktop" if "installed" in data else "web", "saved": youtube_client_secrets_path().exists()}


@app.get("/auth/youtube", dependencies=[Depends(require_token)])
def auth_youtube(request: Request):
    """Start Google sign-in. Works for a local install (redirect to localhost)."""
    from app.utils import auth_helpers

    redirect = str(request.url_for("auth_youtube_callback"))
    try:
        return RedirectResponse(auth_helpers.youtube_auth_url(redirect))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/auth/youtube/callback", name="auth_youtube_callback", response_class=HTMLResponse)
def auth_youtube_callback(request: Request, state: str = "", error: str = ""):
    from app.utils import auth_helpers

    if error:
        return f"<p>YouTube sign-in was cancelled ({error}). You can close this tab.</p>"
    try:
        auth_helpers.youtube_finish(state, str(request.url))
    except Exception as e:
        return HTMLResponse(f"<p>YouTube sign-in failed: {str(e)[:300]}</p>", status_code=400)
    return "<p>✅ YouTube connected. You can close this tab and go back to Clip Agent.</p>"


class InstagramIn(BaseModel):
    user_id: str
    access_token: str
    app_id: str = ""
    app_secret: str = ""


@app.post("/api/publishing/instagram", dependencies=[Depends(require_token)])
def instagram_setup(body: InstagramIn):
    import httpx

    from app.utils.auth_helpers import save_instagram

    s = get_settings()
    try:
        r = httpx.get(f"https://graph.facebook.com/{s.graph_api_version}/{body.user_id}",
                      params={"fields": "username,account_type", "access_token": body.access_token}, timeout=20)
        if r.status_code != 200:
            raise HTTPException(422, f"Instagram rejected these details: {r.text[:300]}")
        info = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach Instagram: {e}")
    save_instagram(body.user_id, body.access_token, body.app_id, body.app_secret)
    return {"ok": True, "username": info.get("username"), "account_type": info.get("account_type")}
