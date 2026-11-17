"""OAuth token storage and refresh logic.

FIXES vs the spec:
  * `GET /auth/youtube/callback` on `http://STATIC_IP:8000` cannot work:
    Google only accepts redirect URIs on https domains or on localhost, never a
    bare public IP over http. YouTube's upload scope is also not available to
    the device-code flow. Authorise once with the "Desktop app" loopback flow
    (scripts/youtube_auth.py, run on the server through `ssh -L 8765:...` or on
    any PC and copy the token file); the API endpoints exist only to report status.
  * Refresh tokens issued while the OAuth consent screen is in "Testing" mode
    expire after 7 days. Set the consent screen to "In production" (unverified
    is fine for your own account) or the pipeline silently stops publishing a
    week later.
  * Instagram long-lived tokens expire after 60 days; they are refreshed
    automatically when older than 30 days.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
# upload + read-only (read-only lets ClipForge show views and estimated campaign earnings)
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube.readonly"]


def youtube_token_path() -> Path:
    return get_settings().secrets_dir / "youtube_token.json"


def youtube_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = youtube_token_path()
    if not path.exists():
        raise RuntimeError("YouTube not authorised: run `python scripts/youtube_auth.py` once")
    creds = Credentials.from_authorized_user_file(str(path))  # keep the scopes that were actually granted
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())  # raises RefreshError if the refresh token was revoked/expired
            path.write_text(creds.to_json())
        else:
            raise RuntimeError("YouTube token invalid and has no refresh token; re-run youtube_auth.py")
    return creds


def run_youtube_loopback_flow(client_secrets: Path, port: int = 8765) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), YOUTUBE_SCOPES)
    creds = flow.run_local_server(host="localhost", port=port, open_browser=False,
                                  access_type="offline", prompt="consent")
    path = youtube_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)
    return path


def _ig_state_path() -> Path:
    return get_settings().secrets_dir / "instagram_token.json"


def instagram_token(client: httpx.Client | None = None) -> str:
    """Current IG token, refreshed (and persisted) when it is >30 days old."""
    s = get_settings()
    path = _ig_state_path()
    state = json.loads(path.read_text()) if path.exists() else {}
    token = state.get("access_token") or s.instagram_access_token
    if not token:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is not set")
    issued = state.get("issued_at", 0)
    if not issued:
        state = {"access_token": token, "issued_at": time.time()}
        path.write_text(json.dumps(state))
        return token
    if time.time() - issued > 30 * 86400 and s.instagram_app_id and s.instagram_app_secret:
        c = client or httpx.Client(timeout=30)
        try:
            # Facebook-login (graph.facebook.com) long-lived token refresh.
            r = c.get(f"https://graph.facebook.com/{s.graph_api_version}/oauth/access_token", params={
                "grant_type": "fb_exchange_token", "fb_exchange_token": token,
                "client_id": s.instagram_app_id, "client_secret": s.instagram_app_secret})
            if r.status_code == 200 and r.json().get("access_token"):
                token = r.json()["access_token"]
                state.update(access_token=token, issued_at=time.time())
                path.write_text(json.dumps(state))
                log.info("instagram token refreshed")
            else:
                log.warning("instagram token refresh failed: %s", r.text[:300])
        finally:
            if client is None:
                c.close()
    return token


# ---------------------------------------------------------------- set up from the web UI
def _ig_account_path() -> Path:
    return get_settings().secrets_dir / "instagram_account.json"


def instagram_user_id() -> str:
    p = _ig_account_path()
    if p.exists():
        uid = json.loads(p.read_text()).get("user_id")
        if uid:
            return str(uid)
    return get_settings().instagram_user_id


def save_instagram(user_id: str, access_token: str, app_id: str = "", app_secret: str = "") -> None:
    d = get_settings().secrets_dir
    d.mkdir(parents=True, exist_ok=True)
    acc = _ig_account_path()
    acc.write_text(json.dumps({"user_id": user_id.strip(), "app_id": app_id.strip(), "app_secret": app_secret.strip()}))
    acc.chmod(0o600)
    tok = _ig_state_path()
    tok.write_text(json.dumps({"access_token": access_token.strip(), "issued_at": time.time()}))
    tok.chmod(0o600)


def youtube_client_secrets_path() -> Path:
    s = get_settings()
    if s.youtube_client_secrets and Path(s.youtube_client_secrets).exists():
        return Path(s.youtube_client_secrets)
    return s.secrets_dir / "client_secret.json"


_pending_flows: dict = {}


def youtube_auth_url(redirect_uri: str) -> str:
    """Browser OAuth for a local install: Google allows http://localhost redirects."""
    import os

    from google_auth_oauthlib.flow import Flow

    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # only localhost http is used
    secrets = youtube_client_secrets_path()
    if not secrets.exists():
        raise RuntimeError("Upload your Google OAuth client JSON first")
    flow = Flow.from_client_secrets_file(str(secrets), scopes=YOUTUBE_SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    _pending_flows[state] = flow
    return url


def youtube_finish(state: str, authorization_response: str) -> Path:
    import os

    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise RuntimeError("This sign-in link expired; press Connect YouTube again")
    flow.fetch_token(authorization_response=authorization_response)
    path = youtube_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(flow.credentials.to_json())
    path.chmod(0o600)
    return path


# ---------------------------------------------------------------- TikTok (Content Posting API)
def _tt_path() -> Path:
    return get_settings().secrets_dir / "tiktok.json"


def tiktok_configured() -> bool:
    p = _tt_path()
    return p.exists() and bool(json.loads(p.read_text()).get("access_token"))


def save_tiktok(client_key: str, client_secret: str, access_token: str, refresh_token: str = "",
                expires_in: int = 86400) -> None:
    p = _tt_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"client_key": client_key.strip(), "client_secret": client_secret.strip(),
                             "access_token": access_token.strip(), "refresh_token": refresh_token.strip(),
                             "expires_at": time.time() + int(expires_in or 86400) - 300}))
    p.chmod(0o600)


def tiktok_token(client: httpx.Client | None = None) -> str:
    """Access tokens last 24 h; refresh tokens 365 days. Refreshed (and saved) when needed."""
    p = _tt_path()
    if not p.exists():
        raise RuntimeError("TikTok is not connected")
    st = json.loads(p.read_text())
    if time.time() < st.get("expires_at", 0) or not (st.get("refresh_token") and st.get("client_key")):
        return st["access_token"]
    c = client or httpx.Client(timeout=30)
    try:
        r = c.post("https://open.tiktokapis.com/v2/oauth/token/", data={
            "client_key": st["client_key"], "client_secret": st.get("client_secret", ""),
            "grant_type": "refresh_token", "refresh_token": st["refresh_token"]})
        d = r.json()
        if r.status_code != 200 or not d.get("access_token"):
            raise RuntimeError(f"TikTok token refresh failed: {r.text[:200]}")
        st.update(access_token=d["access_token"], refresh_token=d.get("refresh_token", st["refresh_token"]),
                  expires_at=time.time() + int(d.get("expires_in", 86400)) - 300)
        p.write_text(json.dumps(st))
        return st["access_token"]
    finally:
        if client is None:
            c.close()


# ---------------------------------------------------------------- Facebook Page (Reels)
def _fb_path() -> Path:
    return get_settings().secrets_dir / "facebook.json"


def facebook_page() -> tuple[str, str]:
    """(page id, page access token). A Page token made from a long-lived user token does not expire."""
    p = _fb_path()
    if not p.exists():
        return "", ""
    st = json.loads(p.read_text())
    return st.get("page_id", ""), st.get("page_token", "")


def facebook_configured() -> bool:
    return all(facebook_page())


def save_facebook(page_id: str, page_token: str) -> None:
    p = _fb_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"page_id": page_id.strip(), "page_token": page_token.strip()}))
    p.chmod(0o600)
