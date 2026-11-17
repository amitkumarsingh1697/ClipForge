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
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def youtube_token_path() -> Path:
    return get_settings().secrets_dir / "youtube_token.json"


def youtube_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = youtube_token_path()
    if not path.exists():
        raise RuntimeError("YouTube not authorised: run `python scripts/youtube_auth.py` once")
    creds = Credentials.from_authorized_user_file(str(path), YOUTUBE_SCOPES)
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
