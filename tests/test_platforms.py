"""Choosing which platforms a clip is posted to (app + Telegram), and Facebook Reels."""
import json

import httpx
from fastapi.testclient import TestClient

from app import pipeline
from app.services import social_publisher as sp
from app.utils.job_store import get_store
from tests.test_features import talky  # noqa: F401  (fixture)

ALL = {"youtube": True, "instagram": True, "facebook": True, "tiktok": False}


def _clip(short_portrait, talky):
    job = pipeline.submit("upload", "p.mp4", raw_path=short_portrait, options={"clip_seconds": 12})
    return get_store().children(job)[0]["id"]


def _fake_uploads(monkeypatch, posted):
    monkeypatch.setattr(sp, "publishers_configured", lambda: dict(ALL))
    monkeypatch.setattr(sp, "upload_youtube", lambda *a, **k: posted.append("youtube") or "yt1")
    monkeypatch.setattr(sp, "upload_instagram", lambda *a, **k: posted.append("instagram") or "ig1")
    monkeypatch.setattr(sp, "instagram_permalink", lambda mid: None)
    monkeypatch.setattr(sp, "upload_facebook", lambda *a, **k: posted.append("facebook") or "fb1")


def test_default_is_all_connected_and_choice_limits_publishing(short_portrait, talky, monkeypatch):
    posted = []
    _fake_uploads(monkeypatch, posted)
    cid = _clip(short_portrait, talky)
    assert pipeline.chosen_platforms(get_store().get(cid)["meta"]) == ["youtube", "instagram", "facebook"]
    assert pipeline.approve(cid, platforms=["instagram"]) == "Approved, publishing now"
    clip = get_store().get(cid)
    assert posted == ["instagram"] and clip["status"] == "published" and clip["meta"]["platforms"] == ["instagram"]


def test_choosing_only_unconnected_platforms_is_refused(short_portrait, talky, monkeypatch):
    _fake_uploads(monkeypatch, [])
    cid = _clip(short_portrait, talky)
    try:
        pipeline.approve(cid, platforms=["tiktok"])
        raise AssertionError("should refuse")
    except ValueError as e:
        assert "connected" in str(e)
    assert get_store().get(cid)["status"] == "awaiting_approval"


def test_facebook_skipped_for_long_clips(short_portrait, talky, monkeypatch):
    posted = []
    _fake_uploads(monkeypatch, posted)
    cid = _clip(short_portrait, talky)
    get_store().update(cid, meta={"duration": 120})
    pipeline.approve(cid, platforms=["youtube", "facebook"])
    assert posted == ["youtube"] and get_store().get(cid)["status"] == "published"
    cid2 = _clip(short_portrait, talky)
    get_store().update(cid2, meta={"duration": 120})
    pipeline.approve(cid2, platforms=["facebook"])  # nothing could be posted -> failed, not "published"
    assert get_store().get(cid2)["status"] == "failed" and "90" in get_store().get(cid2)["error"]


def test_telegram_platform_toggles(short_portrait, talky, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "42")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services import telegram_bot as tb

    _fake_uploads(monkeypatch, [])
    cid = _clip(short_portrait, talky)
    calls = []
    monkeypatch.setattr(tb, "_api", lambda method, **k: calls.append((method, k.get("data"))) or {})
    rows = tb.approval_markup(cid)["inline_keyboard"]
    toggles = [b["text"] for r in rows[2:] for b in r]
    assert toggles == ["☑️ YouTube", "☑️ Instagram", "☑️ Facebook", "🔒 TikTok"]  # TikTok isn't connected
    assert all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r)
    cb = lambda data: {"callback_query": {"id": "1", "from": {"id": 42}, "data": data,
                                          "message": {"chat": {"id": 42}, "message_id": 5}}}
    assert "isn't connected" in tb.handle_update(cb(f"p:{cid}:tt"), None, None, None)
    assert tb.handle_update(cb(f"p:{cid}:yt"), None, None, None) == "Will post to: Instagram, Facebook"
    edited = json.loads([d for m, d in calls if m == "editMessageReplyMarkup"][-1]["reply_markup"])
    texts = [b["text"] for r in edited["inline_keyboard"] for b in r]
    assert "⬜ YouTube" in texts and "☑️ Instagram" in texts
    tb.handle_update(cb(f"p:{cid}:ig"), None, None, None)
    assert "Couldn't" in tb.handle_update(cb(f"p:{cid}:fb"), None, None, None)  # last one stays on
    assert pipeline.chosen_platforms(get_store().get(cid)["meta"]) == ["facebook"]
    tb.handle_update(cb(f"p:{cid}:yt"), None, None, None)
    assert pipeline.chosen_platforms(get_store().get(cid)["meta"]) == ["youtube", "facebook"]


def test_approve_api_takes_platforms(short_portrait, talky, monkeypatch):
    from app.main import app

    posted = []
    _fake_uploads(monkeypatch, posted)
    cid = _clip(short_portrait, talky)
    h = {"X-API-Token": "test-token"}
    client = TestClient(app)
    assert client.post(f"/jobs/{cid}/approve", headers=h, json={"platforms": ["nope"]}).status_code == 422
    r = client.post(f"/jobs/{cid}/approve", headers=h, json={"platforms": ["youtube", "facebook"]})
    assert r.status_code == 200 and sorted(posted) == ["facebook", "youtube"]


def test_upload_facebook_reel_flow(tmp_path, monkeypatch):
    from app.utils import auth_helpers

    auth_helpers.save_facebook("123", "PAGETOKEN")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.host, req.url.path, dict(req.headers)))
        if req.url.path.endswith("/123/video_reels"):
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            if body["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "v9", "upload_url": "u"})
            assert body["video_state"] == "PUBLISHED" and body["video_id"] == "v9"
            return httpx.Response(200, json={"success": True})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["authorization"] == "OAuth PAGETOKEN" and req.headers["file_size"] == "1000"
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"status": {"video_status": "ready"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert sp.upload_facebook(video, "caption #tag", client=c, poll_s=0) == "v9"
    assert [s[1] for s in seen][:3] == ["graph.facebook.com", "rupload.facebook.com", "graph.facebook.com"]
    assert sp.publishers_configured()["facebook"] is True


def test_picker_shown_even_with_nothing_connected(short_portrait, talky, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "42")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services import telegram_bot as tb

    monkeypatch.setattr(sp, "publishers_configured", lambda: {k: False for k in sp.PLATFORM_KEYS})
    cid = _clip(short_portrait, talky)
    texts = [b["text"] for r in tb.approval_markup(cid)["inline_keyboard"][2:] for b in r]
    assert texts == ["🔒 YouTube", "🔒 Instagram", "🔒 Facebook", "🔒 TikTok"]
