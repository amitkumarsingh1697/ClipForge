import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.services import llm, transcriber
from app.utils import runtime_settings
from app.utils.job_store import get_store


def fake_ollama(reply: dict, seen: list | None = None):
    def handler(req: httpx.Request):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen3:8b"}]})
        if req.url.path == "/api/chat":
            body = json.loads(req.content)
            if seen is not None:
                seen.append(body)
            return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_settings_roundtrip_and_validation():
    st = runtime_settings.load()
    assert st["llm_provider"] == "none" and st["whisper_model"] == "small"
    runtime_settings.save({"llm_provider": "ollama", "llm_model": "llama3.1:8b", "llm_api_key": "k"})
    assert runtime_settings.load()["llm_model"] == "llama3.1:8b"
    assert runtime_settings.public(runtime_settings.load())["llm_api_key"] is True
    with pytest.raises(ValueError):
        runtime_settings.save({"llm_provider": "skynet"})


def test_list_models_and_metadata_via_ollama():
    c = fake_ollama({"title": "He Did NOT See That Coming 😳", "caption": "Clean escape.",
                     "hashtags": ["gaming", "#clutch", "epic moments"]})
    assert llm.list_models("ollama", "http://localhost:11434", client=c) == ["llama3.1:8b", "qwen3:8b"]
    cfg = {**runtime_settings.DEFAULTS, "llm_provider": "ollama", "llm_model": "llama3.1:8b"}
    md = llm.clip_metadata("no way", "Stream", "vertical", cfg, client=c)
    assert md["title"].startswith("He Did NOT") and md["hashtags"] == ["#gaming", "#clutch", "#epicmoments"]


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(llm.LLMError):
        llm.parse_json("nope")


def test_openai_compatible_request_shape():
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers.get("authorization"), json.loads(req.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title":"T","caption":"","hashtags":[]}'}}]})

    cfg = {**runtime_settings.DEFAULTS, "llm_provider": "openai", "llm_base_url": "http://localhost:1234/v1",
           "llm_model": "m", "llm_api_key": "sk-x"}
    llm.clip_metadata("", "", "square", cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen[0][0] == "/v1/chat/completions" and seen[0][1] == "Bearer sk-x" and seen[0][2]["model"] == "m"


@pytest.fixture
def words(monkeypatch):
    monkeypatch.setattr(transcriber, "transcribe_words",
                        lambda *a, **k: [transcriber.Word(0.2, 0.8, "insane"), transcriber.Word(0.8, 1.4, "jump")])


def test_render_uses_llm_title_and_hashtags(sample_video, words, monkeypatch):
    runtime_settings.save({"llm_provider": "ollama", "llm_model": "llama3.1:8b"})
    monkeypatch.setattr(llm, "clip_metadata", lambda t, src, fmt, cfg=None: {
        "title": "Insane Jump 🔥", "caption": "Watch till the end.", "hashtags": ["#jump", "#wow"]})
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video, options={"clips": 1})
    c = get_store().children(job)[0]
    assert c["status"] == "awaiting_approval" and c["title"] == "Insane Jump 🔥"
    text = pipeline.caption_text(c["title"], c["meta"])
    assert "Watch till the end." in text and "#jump #wow" in text


def test_llm_failure_falls_back(sample_video, words, monkeypatch):
    runtime_settings.save({"llm_provider": "ollama", "llm_model": "x", "llm_rank": True})

    def boom(*a, **k):
        raise llm.LLMError("connection refused")
    monkeypatch.setattr(llm, "clip_metadata", boom)
    monkeypatch.setattr(llm, "rank_moments", boom)
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video, options={"clips": 1})
    c = get_store().children(job)[0]
    assert c["status"] == "awaiting_approval" and "connection refused" in c["meta"]["llm_error"]
    assert c["start_s"] <= 52 and c["end_s"] >= 58  # audio-best moment still chosen


def test_llm_rank_can_change_the_pick(sample_video, words, monkeypatch):
    runtime_settings.save({"llm_provider": "ollama", "llm_model": "x", "llm_rank": True, "llm_titles": False})
    picked = {}

    def rank(cands, title, cfg=None):
        picked["n"] = len(cands)
        ordered = sorted(cands, key=lambda c: c["score"])
        middle = ordered[1]  # LLM loves the 2nd-loudest moment; equal weighting lets that win
        picked["start"] = middle["start"]
        picked["loudest"] = ordered[-1]["start"]
        return {c["id"]: (10.0 if c is middle else 0.0) for c in cands}
    monkeypatch.setattr(llm, "rank_moments", rank)
    job = pipeline.submit("upload", "g.mp4", raw_path=sample_video, options={"clips": 1, "clip_seconds": 20})
    c = get_store().children(job)[0]
    assert picked["n"] == 3
    assert c["start_s"] == picked["start"] != picked["loudest"]


def test_settings_api(monkeypatch):
    from app.main import app
    client = TestClient(app)
    h = {"X-API-Token": "test-token"}
    assert client.get("/api/settings", headers=h).json()["llm_provider"] == "none"
    r = client.put("/api/settings", headers=h, json={"llm_provider": "ollama", "llm_model": "llama3.1:8b",
                                                       "whisper_model": "base"})
    assert r.status_code == 200 and r.json()["whisper_model"] == "base"
    assert client.put("/api/settings", headers=h, json={"whisper_model": "giant"}).status_code == 422
    monkeypatch.setattr(llm, "list_models", lambda p, u, k="": ["llama3.1:8b"])
    assert client.get("/api/llm/models?provider=ollama&base_url=http://localhost:11434",
                      headers=h).json() == {"models": ["llama3.1:8b"]}

    def down(*a, **k):
        raise llm.LLMError("can't reach ollama")
    monkeypatch.setattr(llm, "list_models", down)
    assert client.get("/api/llm/models?provider=ollama&base_url=x", headers=h).status_code == 502
