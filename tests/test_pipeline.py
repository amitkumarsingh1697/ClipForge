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
    from app.services import social_publisher as sp
    monkeypatch.setattr(sp, "publishers_configured", lambda: {"youtube": True, "instagram": False})
    import app.celery_worker as cw
    monkeypatch.setattr(cw, "publish_clip_task", type("T", (), {"delay": staticmethod(lambda j: calls.append(j))}))
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    cid = get_store().children(job)[0]["id"]
    assert pipeline.approve(cid).startswith("Approved")
    assert pipeline.approve(cid) == "Already publishing"   # double tap
    assert calls == [cid]
    job2 = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    c2 = get_store().children(job2)[0]
    assert pipeline.reject(c2["id"]) == "Rejected and deleted"
    import os
    assert not os.path.exists(c2["output_path"])


def test_approve_without_publishers_saves_clip(sample_video, no_whisper):
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video)
    cid = get_store().children(job)[0]["id"]
    msg = pipeline.approve(cid)
    c = get_store().get(cid)
    assert c["status"] == "approved" and "Connect YouTube, Instagram or TikTok" in msg


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
                     None, None, lambda u, t="": f"queued {u}")
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


def test_live_recording_keeps_only_spike_moments(tmp_path, no_whisper, monkeypatch):
    from tests.conftest import make_video
    # 80 s "stream": quiet for 60 s, loud 62-72 s; no chat for a local file, so audio is the spike signal.
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
        assert "moment_" in c["raw_path"] and "louder" in c["meta"]["live_reason"]
        # only windows around the 62-72 s burst were kept (segments split on keyframes, so allow drift)
        assert 30 <= c["meta"]["stream_offset_s"] <= 75, c["meta"]["stream_offset_s"]
    live_dir = get_settings().live_dir / job
    assert not list(live_dir.glob("buf_*.mp4"))  # the rolling buffer is thrown away
    assert store.get(job)["meta"]["progress"]["stages"][1]["state"] == "done"


def test_chat_spike_detector():
    from app.services.moments import SpikeDetector

    det = SpikeDetector(window_s=10, ratio=2.5, min_msgs=8, warmup_s=60, cooldown_s=45)
    msgs, spikes = [], []
    for sec in range(0, 240):
        n = 1 if sec % 2 == 0 else 0  # ~0.5 msg/s normal chat
        if 180 <= sec < 190:
            n = 6  # everyone types at once
        msgs += [(1000.0 + sec, "LMAO no way 💀" if sec >= 180 else "hello") for _ in range(n)]
        sp = det.check(msgs, now=1000.0 + sec)
        if sp:
            spikes.append(sec)
    assert spikes and 180 <= spikes[0] <= 190 and len(spikes) == 1, spikes


def test_most_replayed_peaks():
    from app.services.moments import heatmap_peaks

    hm = [{"start_time": i * 36.0, "end_time": (i + 1) * 36.0, "value": 0.1} for i in range(100)]
    hm[40]["value"], hm[41]["value"], hm[75]["value"] = 1.0, 0.9, 0.7
    peaks = heatmap_peaks(hm, 3600, 30, 3)
    assert len(peaks) == 2  # 40 and 41 are the same moment
    assert peaks[0][0] < 40 * 36 + 18 < peaks[0][1] and peaks[1][0] < 75 * 36 + 18 < peaks[1][1]
    assert all(round(b - a) == 30 for a, b, _ in peaks)
    assert heatmap_peaks(None, 3600, 30, 3) == []


def test_link_downloads_only_most_replayed_parts(sample_video, monkeypatch, tmp_path):
    from app.services import ingest, moments

    hm = [{"start_time": i * 36.0, "end_time": (i + 1) * 36.0, "value": 0.1} for i in range(100)]
    hm[50]["value"] = 1.0
    monkeypatch.setattr(ingest, "probe_url", lambda u: {"id": "v1", "title": "Big Stream", "duration": 3600,
                                                        "heatmap": hm, "uploader": "Streamer"})
    asked = {}

    def fake_sections(url, ranges, dest, **k):
        asked["ranges"] = ranges
        return [sample_video]
    monkeypatch.setattr(moments, "download_sections", fake_sections)
    monkeypatch.setattr(ingest, "download_url", lambda *a, **k: (_ for _ in ()).throw(AssertionError("full download")))
    job = pipeline.submit("url", "https://www.youtube.com/watch?v=v1", options={"clips": 2, "clip_seconds": 15})
    j = get_store().get(job)
    assert j["status"] == "done", j["error"]
    assert len(asked["ranges"]) == 1 and asked["ranges"][0][0] < 50 * 36 + 18 < asked["ranges"][0][1]
    assert sum(b - a for a, b in asked["ranges"]) < 60  # a minute, not the whole hour
    assert j["meta"]["moment_source"] == "most_replayed" and get_store().children(job)


def test_formats_option_makes_one_clip_per_format(sample_video, no_whisper):
    opts = pipeline.clean_options("vertical,landscape", clips=1)
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video, options=opts)
    clips = get_store().children(job)
    assert sorted(c["meta"]["format"] for c in clips) == ["landscape", "vertical"]
    from app.utils.media import probe
    sizes = {c["meta"]["format"]: (probe(__import__("pathlib").Path(c["output_path"])).width,
                                   probe(__import__("pathlib").Path(c["output_path"])).height) for c in clips}
    assert sizes == {"vertical": (1080, 1920), "landscape": (1920, 1080)}


def test_telegram_link_with_format_words(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "submit", lambda t, u, options=None, **k: seen.update(options=options) or "j1")
    msg = pipeline.submit_link("https://x.com/v", "https://x.com/v square, landscape")
    assert seen["options"]["formats"] == ["square", "landscape"] and "square, landscape" in msg


def test_ui_and_listing_endpoints(sample_video, no_whisper, monkeypatch):
    from app.main import app
    client = TestClient(app)
    assert "ClipForge" in client.get("/").text
    h = {"X-API-Token": "test-token"}
    with sample_video.open("rb") as fh:
        r = client.post("/upload", files={"file": ("a.mp4", fh, "video/mp4")},
                        data={"formats": "square", "clips": "1"}, headers=h)
    assert r.status_code == 200, r.text
    jobs = client.get("/api/jobs", headers=h).json()
    clip = jobs[0]["clips"][0]
    assert clip["meta"]["format"] == "square"
    v = client.get(f"/media/{clip['id']}.mp4?token=test-token")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4"
    assert client.get(f"/media/{clip['id']}.mp4").status_code == 401
    cfg = client.get("/api/config", headers=h).json()
    assert set(cfg["formats"]) == {"vertical", "portrait", "square", "landscape"}
    r = client.post("/ingest/url", json={"url": "https://e.com/v.mp4", "formats": ["diagonal"]}, headers=h)
    assert r.status_code == 422


def test_no_token_configured_allows_only_localhost(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    assert TestClient(app).get("/api/config").status_code == 200          # testclient counts as local
    assert TestClient(app, client=("203.0.113.5", 5000)).get("/api/config").status_code == 503


def test_thread_mode_runs_in_background(sample_video, no_whisper, monkeypatch):
    import time
    monkeypatch.delenv("CELERY_EAGER")
    monkeypatch.setenv("TASK_MODE", "thread")
    from app.config import get_settings
    get_settings.cache_clear()
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video, options={"clips": 1})
    deadline = time.time() + 120
    while time.time() < deadline:
        cl = get_store().children(job)
        if cl and cl[0]["status"] in ("awaiting_approval", "failed"):
            break
        time.sleep(1)
    assert cl[0]["status"] == "awaiting_approval", cl[0]["error"]


def test_network_error_retries_ipv4_then_explains(monkeypatch, tmp_path):
    import yt_dlp
    from app.services import ingest
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(opts.get("source_address"))
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError("Unable to download API page: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(ConnectionError, match="connection to the site was cut off"):
        ingest.download_url("https://www.youtube.com/watch?v=x", tmp_path)
    assert calls == [None, "0.0.0.0"]


def test_non_network_download_error_is_not_retried(monkeypatch, tmp_path):
    import yt_dlp
    from app.services import ingest
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(1)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError("Video unavailable. This video is private")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(yt_dlp.utils.DownloadError):
        ingest.download_url("https://www.youtube.com/watch?v=x", tmp_path)
    assert calls == [1]


def test_live_resolver_handles_separate_streams_and_bad_formats(monkeypatch):
    import yt_dlp

    from app.services import ingest

    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            calls.append(self.opts.get("format"))
            if self.opts.get("format"):  # our format string fails, yt-dlp's default works
                raise yt_dlp.utils.DownloadError("Requested format is not available. Use --list-formats for a "
                                                 "list of available formats")
            return {"live_status": "is_live", "format_id": "299+140",
                    "requested_formats": [{"url": "https://v.example/video.m3u8", "http_headers": {"User-Agent": "x"}},
                                          {"url": "https://v.example/audio.m3u8"}]}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    urls, headers = ingest.resolve_streams("https://www.youtube.com/watch?v=abc")
    assert urls == ["https://v.example/video.m3u8", "https://v.example/audio.m3u8"] and headers["User-Agent"] == "x"
    assert calls[0] and calls[1] is None
    assert "hasn't started" in ingest.live_error_message("x", {"live_status": "is_upcoming"})
    assert "Link" in ingest.live_error_message("x", {"live_status": "was_live"})


def test_youtube_chat_lines_are_read():
    import json
    import time

    from app.services.moments import YouTubeChat

    from pathlib import Path

    c = YouTubeChat("https://youtube.com/watch?v=x", Path("/tmp"))
    now_us = int(time.time() * 1e6)
    line = {"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "message": {"runs": [{"text": "NO WAY"}, {"emoji": {"emojiId": "💀", "shortcuts": [":skull:"]}}]},
        "timestampUsec": str(now_us)}}}}]}, "isLive": True}
    c._parse(json.dumps(line).encode())
    assert len(c.messages) == 1 and c.messages[0][1] == "NO WAY 💀" and abs(c.messages[0][0] - now_us / 1e6) < 1
