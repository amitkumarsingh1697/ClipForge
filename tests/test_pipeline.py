import http.server
import shutil
import threading
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.services import transcriber
from app.utils.job_store import get_store


@pytest.fixture
def no_whisper(monkeypatch):
    monkeypatch.setattr(transcriber, "transcribe_words",
                        lambda *a, **k: [transcriber.Word(0.2, 0.8, "let's"), transcriber.Word(0.8, 1.4, "go")])


@pytest.fixture
def http_server(sample_video, tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    shutil.copy(sample_video, root / "match.mp4")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                          partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    srv.RequestHandlerClass.log_message = lambda *a: None
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_url_ingestion_end_to_end(http_server, no_whisper, monkeypatch):
    monkeypatch.setenv("CLIPS_PER_SOURCE", "2")
    from app.config import get_settings
    get_settings.cache_clear()
    job = pipeline.submit("url", f"{http_server}/match.mp4")
    store = get_store()
    src = store.get(job)
    assert src["status"] == "done", src["error"]
    clips = store.children(job)
    assert len(clips) == 2
    for c in clips:
        assert c["status"] == "awaiting_approval", c["error"]
        assert c["meta"]["caption_words"] == 2
    assert any(c["start_s"] <= 52 and c["end_s"] >= 58 for c in clips)


def test_approve_is_idempotent_and_reject_deletes(sample_video, no_whisper, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "publish_clip", lambda jid: calls.append(jid))
    import app.celery_worker as cw
    monkeypatch.setattr(cw, "publish_clip_task", type("T", (), {"delay": staticmethod(lambda j: calls.append(j))}))
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    clips = get_store().children(job)
    cid = clips[0]["id"]
    assert pipeline.approve(cid).startswith("Approved")
    assert pipeline.approve(cid) == "Already publishing"   # double tap
    assert calls == [cid]
    # reject on another fresh clip
    job2 = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    c2 = get_store().children(job2)[0]
    assert pipeline.reject(c2["id"]) == "Rejected and deleted"
    import os
    assert not os.path.exists(c2["output_path"])


def test_publish_without_credentials_fails_clearly(sample_video, no_whisper):
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    cid = get_store().children(job)[0]["id"]
    pipeline.approve(cid)  # eager: runs publish inline
    c = get_store().get(cid)
    assert c["status"] == "failed" and "no publisher configured" in c["error"]


def test_api_requires_token_and_accepts_upload(sample_video, no_whisper):
    from app.main import app
    client = TestClient(app)
    with sample_video.open("rb") as fh:
        r = client.post("/upload", files={"file": ("clip.mp4", fh, "video/mp4")})
    assert r.status_code == 401
    r = client.post("/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")},
                    headers={"X-API-Token": "test-token"})
    assert r.status_code == 415
    with sample_video.open("rb") as fh:
        r = client.post("/upload", files={"file": ("my clip.mp4", fh, "video/mp4")},
                        headers={"X-API-Token": "test-token"})
    assert r.status_code == 200 and r.json()["status"] == "processing"
    jid = r.json()["job_id"]
    r = client.get(f"/jobs/{jid}", headers={"X-API-Token": "test-token"})
    assert r.json()["clips"][0]["status"] == "awaiting_approval"
    assert client.post("/telegram-webhook", json={}).status_code == 401


def test_telegram_callback_only_from_owner(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "42")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services import telegram_bot as tb
    monkeypatch.setattr(tb, "_api", lambda *a, **k: {})
    seen = []
    upd = lambda uid: {"callback_query": {"id": "1", "from": {"id": uid}, "data": "a:abc123",
                                          "message": {"chat": {"id": uid}, "message_id": 5}}}
    assert tb.handle_update(upd(7), seen.append, seen.append, seen.append) == "ignored: not owner"
    tb.handle_update(upd(42), lambda j: seen.append(j) or "ok", seen.append, seen.append)
    assert seen == ["abc123"]
    sent = []
    monkeypatch.setattr(tb, "send_text", lambda t, chat_id=None: sent.append(t))
    tb.handle_update({"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "clip this https://x.com/v/1 pls"}},
                     None, None, lambda u: f"queued {u}")
    assert sent == ["queued https://x.com/v/1"]


def test_instagram_resumable_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1789")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services import social_publisher as sp
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 1000)
    log, polls = [], iter(["IN_PROGRESS", "FINISHED"])

    def handler(req: httpx.Request):
        log.append((req.method, req.url.host, req.url.path))
        if req.url.path.endswith("/1789/media"):
            return httpx.Response(200, json={"id": "C1"})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["file_size"] == "1000" and req.headers["authorization"] == "OAuth tok"
            return httpx.Response(200, json={"success": True})
        if req.url.path.endswith("/C1"):
            return httpx.Response(200, json={"status_code": next(polls)})
        if req.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M9"})
        return httpx.Response(404)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    assert sp.upload_instagram(video, "cap", client=c, poll_s=0) == "M9"
    assert [p for _, _, p in log][-1].endswith("media_publish")


def test_live_recording_cuts_highlight_segments(tmp_path, no_whisper, monkeypatch):
    from tests.conftest import make_video
    # 80 s "stream": quiet for 60 s, loud 62-72 s; 10 s segments.
    src = make_video(tmp_path / "stream.mp4", duration=80, burst=(62, 72))
    monkeypatch.setenv("LIVE_SEGMENT_S", "10")
    from app.config import get_settings
    get_settings.cache_clear()
    store = get_store()
    job = store.create("source", source_type="live", source=str(src), title="Stream")
    clips = pipeline.record_live(job)
    assert store.get(job)["status"] == "done", store.get(job)["error"]
    assert clips, store.get(job)["meta"]
    for cid in clips:
        c = store.get(cid)
        assert c["status"] == "awaiting_approval", c["error"]
        assert "seg_0000" in c["raw_path"]
    segs = sorted(int(store.get(c)["raw_path"][-9:-4]) for c in clips)
    # Stream-copy segments split on keyframes, so boundaries drift a few seconds;
    # what matters is that only segments overlapping the 62-72 s burst became clips.
    assert segs and min(segs) >= 5, segs
