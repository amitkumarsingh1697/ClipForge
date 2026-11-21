"""ClipForge features: copyright check, LLM providers, campaigns, editing, jump cuts, scheduling, Telegram edits."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.services import llm, rights, transcriber, video_editor
from app.utils import campaigns, runtime_settings
from app.utils.job_store import get_store

W = transcriber.Word


@pytest.fixture
def talky(monkeypatch):
    words = [W(0.3, 0.7, "this"), W(0.7, 1.1, "is"), W(1.1, 1.6, "insane!"), W(4.0, 4.4, "wait"),
             W(4.4, 4.9, "for"), W(4.9, 5.5, "it"), W(6.2, 6.8, "no"), W(6.8, 7.4, "way"), W(7.4, 8.0, "bro"),
             W(9.0, 9.5, "boom")]
    monkeypatch.setattr(transcriber, "transcribe_words", lambda *a, **k: list(words))
    return words


# ---------------------------------------------------------------- copyright check
def test_rights_music_label_is_high_and_cc_is_low():
    hi = rights.assess({"channel": "Universal Music Group VEVO", "categories": ["Music"], "track": "Song",
                        "channel_follower_count": 5_000_000, "extractor_key": "Youtube", "title": "Official Video"})
    assert hi["risk"] == "high" and hi["monetised"] == "likely"
    assert any("Content ID" in r for r in hi["reasons"]) and any("music" in a.lower() for a in hi["advice"])
    lo = rights.assess({"channel": "Some Gamer", "license": "Creative Commons Attribution license (reuse allowed)",
                        "categories": ["Gaming"], "channel_follower_count": 300, "extractor_key": "Youtube"})
    assert lo["risk"] == "low" and lo["monetised"] == "unlikely"


def test_rights_upload_without_info_is_unknown_and_tv_filename_is_high():
    assert rights.assess({"title": "my gameplay"}, "upload")["risk"] == "unknown"
    assert rights.assess({"title": "Naruto Episode 12 full episode"}, "upload")["risk"] == "high"


def test_rights_check_endpoint(monkeypatch):
    from app.main import app
    from app.services import ingest

    monkeypatch.setattr(ingest, "probe_url", lambda url: {"title": "Stream", "channel": "Streamer",
                                                          "extractor_key": "TwitchStream", "is_live": True,
                                                          "channel_follower_count": 20_000})
    r = TestClient(app).post("/api/rights/check", headers={"X-API-Token": "test-token"},
                             json={"url": "https://twitch.tv/streamer"})
    assert r.status_code == 200 and r.json()["is_live"] and r.json()["risk"] in ("low", "medium")


# ---------------------------------------------------------------- LLM providers
def test_claude_provider_uses_messages_api_and_per_provider_keys():
    runtime_settings.save({"llm_provider": "claude", "llm_model": "claude-haiku-5-5", "llm_api_key": "sk-ant"})
    runtime_settings.save({"llm_provider": "gemini", "llm_model": "gemini-2.5-flash", "llm_api_key": "g-key"})
    st = runtime_settings.load()
    assert st["llm_keys"] == {"claude": "sk-ant", "gemini": "g-key"}
    pub = runtime_settings.public(st)
    assert "llm_keys" not in pub and pub["llm_keys_set"] == ["claude", "gemini"] and "claude" in pub["providers"]
    seen = []

    def handler(req):
        seen.append((str(req.url), req.headers.get("x-api-key"), json.loads(req.content)))
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(
            {"hook": "He did NOT expect this", "title": "T1", "alt_hooks": ["Wait for it"],
             "alt_titles": ["T2", "T3"], "caption": "c", "hashtags": ["a"], "keywords": [], "virality": 80})}]})

    cfg = {**runtime_settings.load(), "llm_provider": "claude", "llm_model": "claude-haiku-5-5", "llm_base_url": ""}
    md = llm.clip_metadata("x", "y", "vertical", cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    url, key, body = seen[0]
    assert url == "https://api.anthropic.com/v1/messages" and key == "sk-ant" and body["model"] == "claude-haiku-5-5"
    assert md["hook_options"] == ["He did NOT expect this", "Wait for it"] and md["title_options"] == ["T1", "T2", "T3"]


def test_openai_compatible_falls_back_without_json_mode():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format not supported")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title":"ok"}'}}]})

    cfg = {**runtime_settings.DEFAULTS, "llm_provider": "kimi", "llm_model": "k", "llm_keys": {"kimi": "x"}}
    out = llm.chat_json("s", "u", cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert out == {"title": "ok"} and calls == [True, False]


# ---------------------------------------------------------------- jump cuts / zoom
def test_jump_cut_spans_and_remap(talky):
    keep = video_editor.jump_cut_spans(talky, 12.0, max_removed=0.5)
    assert keep == [(0.0, 1.75), (3.85, 9.65)]
    assert video_editor.remap_time(4.0, keep) == pytest.approx(1.9)
    assert video_editor.remap_time(2.5, keep) is None
    # only gaps the checker calls dead are cut (a silent fight scene stays)
    assert video_editor.jump_cut_spans(talky, 12.0, max_removed=0.5, gap_is_dead=lambda a, b: a < 5) == \
        [(0.0, 1.75), (3.85, 12.0)]
    # at most 25% of the clip is removed by default: longest dead gap first
    assert video_editor.jump_cut_spans(talky, 12.0) == [(0.0, 9.65)]
    assert video_editor.jump_cut_spans(talky[:3], 12.0) is None  # too little speech: leave it alone
    z = pipeline.zoom_times(talky, {"boom"}, 12.0)
    assert z == [(8.95, 10.1)]


def test_dead_gap_checker_keeps_silent_action(tmp_path):
    import subprocess

    src = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=16",
                    "-f", "lavfi", "-i", "color=c=white:s=40x40:r=30:d=16",
                    "-f", "lavfi", "-i", "sine=f=300:d=16",
                    "-filter_complex", "[0:v][1:v]overlay=x='if(between(t,8,12),mod(t*400,280),20)':y=100:eval=frame[v];"
                    "[2:a]volume='if(between(t,4,12),0,1)':eval=frame[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(src)], check=True)
    dead = video_editor.dead_gap_checker(src, 0, 16)
    assert dead(4.2, 7.8) is True      # calm and quiet: real dead air
    assert dead(8.2, 11.8) is False    # quiet but lots of motion: action, keep it
    assert dead(0.5, 3.5) is False     # loud


def test_render_with_cuts_zoom_and_cover_intro(short_portrait, tmp_path):
    out = video_editor.render_clip(short_portrait, tmp_path / "o.mp4", 0, 10, out_w=1080, out_h=1920,
                                   keep=[(0, 3), (5, 9)], zoom_times=[(1, 2)], progress_bar=True,
                                   intro_image=None)
    assert abs(video_editor.probe(out).duration - 7.0) < 0.2
    cover = tmp_path / "c.jpg"
    video_editor.make_cover(short_portrait, cover, 2, 1080, 1920)
    out2 = video_editor.render_clip(short_portrait, tmp_path / "o2.mp4", 0, 5, out_w=1080, out_h=1920,
                                    intro_image=cover, intro_s=0.25)
    info = video_editor.probe(out2)
    assert abs(info.duration - 5.25) < 0.2 and info.has_audio


def test_pipeline_clip_has_cuts_cover_intro_and_watermark(short_portrait, talky, monkeypatch):
    monkeypatch.setattr(video_editor, "dead_gap_checker", lambda *a, **k: (lambda a, b: True))
    runtime_settings.save({"watermark": "@peakmoments"})
    job = pipeline.submit("upload", "p.mp4", raw_path=short_portrait, options={"clip_seconds": 12})
    c = get_store().children(job)[0]
    m = c["meta"]
    assert c["status"] == "awaiting_approval" and m["cover_offset_ms"] == 0 and m["cover_path"]
    assert m["cuts"] >= 1 and m["seconds_removed"] > 0
    assert get_store().get(job)["meta"]["rights"]["risk"] == "unknown"


# ---------------------------------------------------------------- editing
def test_edit_title_is_instant_and_hook_rerenders(short_portrait, talky):
    job = pipeline.submit("upload", "p.mp4", raw_path=short_portrait, options={"clip_seconds": 12})
    cid = get_store().children(job)[0]["id"]
    assert "title" in pipeline.edit_clip(cid, title="My own title", caption="Own caption")
    c = get_store().get(cid)
    assert c["title"] == "My own title" and c["meta"]["title_locked"]
    assert "Own caption" in pipeline.caption_text(c["title"], c["meta"])
    msg = pipeline.edit_clip(cid, hook="Watch this", cover_time=2)
    c = get_store().get(cid)
    assert "Re-making" in msg and c["status"] == "awaiting_approval"
    assert c["meta"]["hook_text"] == "Watch this" and c["title"] == "My own title"
    with pytest.raises(ValueError):
        get_store().update(cid, status="published")
        pipeline.edit_clip(cid, title="x")


def test_cover_upload_endpoint(short_portrait, talky, tmp_path):
    from app.main import app

    job = pipeline.submit("upload", "p.mp4", raw_path=short_portrait, options={"clip_seconds": 12})
    cid = get_store().children(job)[0]["id"]
    img = tmp_path / "mine.jpg"
    video_editor.make_cover(short_portrait, img, 1, 400, 400)
    client = TestClient(app)
    h = {"X-API-Token": "test-token"}
    assert client.post(f"/jobs/{cid}/cover", headers=h, files={"file": ("x.txt", b"hello")}).status_code == 415
    r = client.post(f"/jobs/{cid}/cover", headers=h, files={"file": ("mine.jpg", img.read_bytes(), "image/jpeg")})
    assert r.status_code == 200
    assert get_store().get(cid)["meta"]["cover_image"].endswith("_custom_cover.jpg")
    r = client.patch(f"/jobs/{cid}", headers=h, json={"title": "API title"})
    assert r.status_code == 200 and get_store().get(cid)["title"] == "API title"


# ---------------------------------------------------------------- scheduling + campaigns
def test_schedule_then_publish_when_due(short_portrait, talky, monkeypatch):
    from app.services import social_publisher as sp

    monkeypatch.setattr(sp, "publishers_configured", lambda: {"youtube": True, "instagram": False, "tiktok": False})
    posted = []
    monkeypatch.setattr(sp, "upload_youtube", lambda video, title, caption, **k: posted.append(caption) or "vid1")
    c = campaigns.upsert({"name": "Streamer X", "marketplace": "whop", "cpm": 2, "hashtags": "streamerx #ad",
                          "mention": "streamerx"})
    job = pipeline.submit("upload", "p.mp4", raw_path=short_portrait, options={"clip_seconds": 12, "campaign": c["id"]})
    cid = get_store().children(job)[0]["id"]
    assert pipeline.approve(cid, "2099-01-01T10:00:00Z").startswith("Scheduled")
    assert pipeline.run_due_schedules() == 0 and get_store().get(cid)["status"] == "scheduled"
    get_store().update(cid, meta={"publish_at": "2000-01-01T00:00:00+00:00"})
    assert pipeline.run_due_schedules() == 1
    clip = get_store().get(cid)
    assert clip["status"] == "published" and clip["meta"]["youtube_id"] == "vid1"
    assert "#streamerx #ad" in posted[0] and "@streamerx" in posted[0]  # campaign tags always kept
    monkeypatch.setattr(sp, "youtube_views", lambda ids: {"vid1": 12_000})
    st = pipeline.refresh_stats()
    assert st["views"] == 12_000 and st["earnings_est"] == 24.0


# ---------------------------------------------------------------- telegram edit flow
def test_telegram_edit_title_by_button_and_typed_text(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "42")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.services import telegram_bot as tb

    asked = []
    monkeypatch.setattr(tb, "_api", lambda method, **k: asked.append((method, k.get("data"))) or {})
    monkeypatch.setattr(tb, "send_text", lambda t, chat_id=None: asked.append(("text", t)))
    monkeypatch.setattr(tb, "_clip_options", lambda jid, key: ["Alt A", "Alt B"])
    edits = []
    on_edit = lambda jid, **k: edits.append((jid, k)) or "done"
    cb = lambda data: {"callback_query": {"id": "1", "from": {"id": 42}, "data": data,
                                          "message": {"chat": {"id": 42}, "message_id": 5}}}
    tb.handle_update(cb("et:abc"), None, None, None, on_edit)
    assert any(m == "sendMessage" and "Alt B" in d["reply_markup"] for m, d in asked if m == "sendMessage")
    tb.handle_update(cb("ts:abc:1"), None, None, None, on_edit)
    assert edits[-1] == ("abc", {"title": "Alt B"})
    tb.handle_update(cb("ec:abc"), None, None, None, on_edit)
    tb.handle_update({"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "12"}}, None, None, None, on_edit)
    assert edits[-1] == ("abc", {"cover_time": 12.0})
    tb.handle_update(cb("eh:abc"), None, None, None, on_edit)
    tb.handle_update({"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "none"}}, None, None, None, on_edit)
    assert edits[-1] == ("abc", {"hook": ""})
    # without a pending edit, plain text is ignored
    assert tb.handle_update({"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "hello"}},
                            None, None, None, on_edit) is None


def test_render_cuts_and_intro_together(short_portrait, tmp_path):
    """Regression: jump cuts + zoom + progress bar + cover intro in one render used to stall ffmpeg on macOS."""
    cover = tmp_path / "c.jpg"
    video_editor.make_cover(short_portrait, cover, 2, 1080, 1920)
    out = video_editor.render_clip(short_portrait, tmp_path / "o.mp4", 0, 10, out_w=1080, out_h=1920,
                                   keep=[(0, 3), (5, 9)], zoom_times=[(1, 2)], progress_bar=True,
                                   intro_image=cover, intro_s=0.25)
    info = video_editor.probe(out)
    assert abs(info.duration - 7.25) < 0.25 and info.has_audio and (info.width, info.height) == (1080, 1920)
    assert not list(tmp_path.glob("o_*"))  # temp body / intro / list files cleaned up


def test_logs_never_contain_tokens(caplog):
    import logging

    from app.utils.logs import RedactFilter, redact

    assert "AAH" not in redact("https://api.telegram.org/bot123456789:AAHabcdefghijklmnopqrstuvwxyz0123/getUpdates")
    log = logging.getLogger("t")
    caplog.handler.addFilter(RedactFilter())
    log.warning("failed: %s", "https://api.telegram.org/bot123456789:AAHabcdefghijklmnopqrstuvwxyz0123/x")
    assert "AAH" not in caplog.text and "bot***" in caplog.text
