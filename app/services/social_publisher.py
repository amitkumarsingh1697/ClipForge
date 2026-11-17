"""YouTube and Instagram API logic.

FIXES vs the spec:
  YouTube
  * Uploads use the resumable protocol (large files over flaky links).
  * The daily quota is 10,000 units and one upload costs ~1,600, so about 6
    uploads/day. quotaExceeded is reported clearly instead of a stack trace.
  * Apps that haven't passed Google's API audit have uploads forced to
    private. Privacy is configurable (default private) and the result says so.
  * "#Shorts" is added and the clip must be vertical and <= 3 min to be a Short.
  Instagram
  * The Graph API cannot take a file from disk the way the spec assumed: it
    needs either a public HTTPS `video_url` or Meta's resumable upload. This
    uses the resumable upload (rupload.facebook.com), so the server needs no
    public hosting.
  * Publishing requires an Instagram *Business or Creator* account linked to a
    Facebook Page; personal accounts are rejected by the API.
  * A container must finish processing (status_code FINISHED) before
    media_publish, otherwise the call fails; the status is polled.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from app.config import get_settings
from app.utils.auth_helpers import instagram_token, youtube_credentials

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


def youtube_title(title: str) -> str:
    t = " ".join(title.split())
    if "#shorts" not in t.lower():
        t = f"{t} #Shorts"
    return t[:100]


def upload_youtube(video: Path, title: str, description: str, tags: list[str] | None = None) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    s = get_settings()
    yt = build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)
    body = {
        "snippet": {"title": youtube_title(title), "description": description[:4900],
                    "tags": (tags or [])[:30], "categoryId": s.youtube_category_id},
        "status": {"privacyStatus": s.youtube_privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response, retries = None, 0
    while response is None:
        try:
            _status, response = req.next_chunk()
        except HttpError as e:
            reason = str(e)
            if "quotaExceeded" in reason or "uploadLimitExceeded" in reason:
                raise PublishError("YouTube daily upload quota reached; try again after midnight Pacific") from e
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                time.sleep(2 ** retries)
                continue
            raise PublishError(f"YouTube upload failed: {reason[:300]}") from e
    vid = response["id"]
    log.info("youtube upload ok: %s (%s)", vid, s.youtube_privacy)
    return vid


def upload_instagram(video: Path, caption: str, client: httpx.Client | None = None,
                     poll_s: float = 5.0, timeout_s: float = 600) -> str:
    s = get_settings()
    if not s.instagram_user_id:
        raise PublishError("INSTAGRAM_USER_ID is not set (must be a Business/Creator account id)")
    c = client or httpx.Client(timeout=120)
    token = instagram_token(c)
    graph = f"https://graph.facebook.com/{s.graph_api_version}"
    try:
        r = c.post(f"{graph}/{s.instagram_user_id}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
            "share_to_feed": "true", "access_token": token})
        if r.status_code != 200:
            raise PublishError(f"IG container create failed: {r.text[:300]}")
        container = r.json()["id"]
        size = video.stat().st_size
        with video.open("rb") as fh:
            r = c.post(f"https://rupload.facebook.com/ig-api-upload/{s.graph_api_version}/{container}",
                       headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
                       content=fh.read(), timeout=600)
        if r.status_code != 200 or not r.json().get("success", False):
            raise PublishError(f"IG upload failed: {r.text[:300]}")
        deadline = time.time() + timeout_s
        while True:
            st = c.get(f"{graph}/{container}", params={"fields": "status_code,status", "access_token": token}).json()
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"IG processing failed: {st.get('status')}")
            if time.time() > deadline:
                raise PublishError("IG processing timed out")
            time.sleep(poll_s)
        r = c.post(f"{graph}/{s.instagram_user_id}/media_publish",
                   data={"creation_id": container, "access_token": token})
        if r.status_code != 200:
            raise PublishError(f"IG publish failed: {r.text[:300]}")
        media_id = r.json()["id"]
        log.info("instagram publish ok: %s", media_id)
        return media_id
    finally:
        if client is None:
            c.close()


def publishers_configured() -> dict[str, bool]:
    s = get_settings()
    from app.utils.auth_helpers import youtube_token_path

    return {"youtube": youtube_token_path().exists(),
            "instagram": bool(s.instagram_user_id and (s.instagram_access_token or
                                                       (s.secrets_dir / "instagram_token.json").exists()))}
