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


def youtube_title(title: str, shorts: bool = True) -> str:
    t = " ".join(title.split())
    if shorts and "#shorts" not in t.lower():
        t = f"{t} #Shorts"
    return t[:100]


def upload_youtube(video: Path, title: str, description: str, tags: list[str] | None = None,
                   shorts: bool = True, thumbnail: Path | None = None) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    s = get_settings()
    yt = build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)
    body = {
        "snippet": {"title": youtube_title(title, shorts), "description": description[:4900],
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
    if thumbnail is not None and thumbnail.exists():
        # Needs a phone-verified channel. Shorts mostly show the first frame instead, which is why
        # ClipForge also puts the cover at the start of the clip. Never fail the upload over it.
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
        except HttpError as e:
            log.warning("youtube thumbnail not set: %s", e)
    return vid


def upload_instagram(video: Path, caption: str, client: httpx.Client | None = None,
                     poll_s: float = 5.0, timeout_s: float = 600, thumb_offset_ms: int | None = None) -> str:
    from app.utils.auth_helpers import instagram_user_id

    s = get_settings()
    ig_user = instagram_user_id()
    if not ig_user:
        raise PublishError("INSTAGRAM_USER_ID is not set (must be a Business/Creator account id)")
    c = client or httpx.Client(timeout=120)
    token = instagram_token(c)
    graph = f"https://graph.facebook.com/{s.graph_api_version}"
    try:
        form = {"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
                "share_to_feed": "true", "access_token": token}
        if thumb_offset_ms is not None:
            form["thumb_offset"] = str(max(0, int(thumb_offset_ms)))  # cover frame
        r = c.post(f"{graph}/{ig_user}/media", data=form)
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
        r = c.post(f"{graph}/{ig_user}/media_publish",
                   data={"creation_id": container, "access_token": token})
        if r.status_code != 200:
            raise PublishError(f"IG publish failed: {r.text[:300]}")
        media_id = r.json()["id"]
        log.info("instagram publish ok: %s", media_id)
        return media_id
    finally:
        if client is None:
            c.close()


def instagram_permalink(media_id: str) -> str | None:
    s = get_settings()
    try:
        r = httpx.get(f"https://graph.facebook.com/{s.graph_api_version}/{media_id}",
                      params={"fields": "permalink", "access_token": instagram_token()}, timeout=20)
        return r.json().get("permalink")
    except Exception:
        return None


def upload_tiktok(video: Path, title: str, cover_ms: int = 0, client: httpx.Client | None = None,
                  poll_s: float = 5.0, timeout_s: float = 600) -> str:
    """TikTok Content Posting API, direct post from a local file (chunked upload).
    Apps that haven't passed TikTok's audit can only post privately (SELF_ONLY)."""
    from app.utils.auth_helpers import tiktok_token

    s = get_settings()
    c = client or httpx.Client(timeout=120)
    api = "https://open.tiktokapis.com/v2/post/publish"
    try:
        token = tiktok_token(c)
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
        size = video.stat().st_size
        chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
        count = max(1, size // chunk)
        r = c.post(f"{api}/video/init/", headers=h, json={
            "post_info": {"title": title[:2200], "privacy_level": s.tiktok_privacy,
                          "video_cover_timestamp_ms": int(cover_ms or 0)},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count}})
        d = r.json()
        if r.status_code != 200 or d.get("error", {}).get("code") not in (None, "ok"):
            raise PublishError(f"TikTok init failed: {r.text[:300]}")
        publish_id, upload_url = d["data"]["publish_id"], d["data"]["upload_url"]
        with video.open("rb") as fh:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                fh.seek(start)
                body = fh.read(end - start + 1)
                r = c.put(upload_url, content=body, timeout=600, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise PublishError(f"TikTok upload failed: {r.status_code} {r.text[:200]}")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = c.post(f"{api}/status/fetch/", headers=h, json={"publish_id": publish_id}).json()
            status = (st.get("data") or {}).get("status")
            if status == "PUBLISH_COMPLETE":
                return publish_id
            if status == "FAILED":
                raise PublishError(f"TikTok processing failed: {(st.get('data') or {}).get('fail_reason')}")
            time.sleep(poll_s)
        return publish_id  # still processing; TikTok finishes it in the background
    finally:
        if client is None:
            c.close()


def youtube_views(video_ids: list[str]) -> dict[str, int]:
    from googleapiclient.discovery import build

    if not video_ids:
        return {}
    yt = build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)
    out = {}
    for i in range(0, len(video_ids), 50):
        res = yt.videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for it in res.get("items", []):
            out[it["id"]] = int(it.get("statistics", {}).get("viewCount", 0))
    return out


def instagram_views(media_id: str) -> int | None:
    s = get_settings()
    token = instagram_token()
    for metric in ("views", "plays"):
        try:
            r = httpx.get(f"https://graph.facebook.com/{s.graph_api_version}/{media_id}/insights",
                          params={"metric": metric, "access_token": token}, timeout=20)
            data = r.json().get("data") or []
            if r.status_code == 200 and data:
                return int(data[0]["values"][0]["value"])
        except Exception:
            continue
    return None


def publishers_configured() -> dict[str, bool]:
    s = get_settings()
    from app.utils.auth_helpers import instagram_user_id, tiktok_configured, youtube_token_path

    return {"youtube": youtube_token_path().exists(), "tiktok": tiktok_configured(),
            "instagram": bool(instagram_user_id() and (s.instagram_access_token or
                                                       (s.secrets_dir / "instagram_token.json").exists()))}
