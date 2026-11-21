"""Viral packaging and editing: SEO text, payoff tease/replay, beat, and step-by-step progress."""
from pathlib import Path

from app import pipeline
from app.services import music, seo, transcriber, video_editor
from app.utils.job_store import get_store
from app.utils.media import probe


def test_youtube_privacy_defaults_to_public():
    from app.config import Settings

    assert Settings().youtube_privacy == "public"


def test_hashtags_specific_first_and_capped():
    tags = seo.hashtags(llm_tags=["#GTA6", "#KaiCenat"], source_title="GTA 6 MARATHON | Kai Cenat",
                        transcript="bro the plane just crashed into the building", default_tags="#shorts",
                        limit=20)
    assert tags[:2] == ["#GTA6", "#KaiCenat"]
    assert "#gaming" in tags and "#shorts" in tags and len(tags) <= 20
    assert len({t.lower() for t in tags}) == len(tags)  # no duplicates
    ig = seo.hashtags(llm_tags=["#GTA6"], source_title="GTA 6", campaign_tags="#ad #brand", limit=5)
    assert len(ig) == 5 and "#ad" in ig and "#brand" in ig  # campaign tags always kept


def test_niches_and_keywords():
    assert seo.detect_niches("GTA 6 First Time played by Kai Cenat")[:2] == ["streamer", "gta"] or \
        set(seo.detect_niches("GTA 6 First Time played by Kai Cenat")) >= {"gta", "streamer"}
    kws = seo.keywords("GTA 6 MARATHON | Kai Cenat", "kai is driving the plane, the plane goes down")
    assert any("Kai Cenat" in k for k in kws)
    assert seo.tagify("Kai Cenat") == "#KaiCenat"


def test_post_package_is_detailed():
    meta = {"hook_text": "He did NOT expect this", "transcript": "the plane crashed into the tower",
            "source_info": {"title": "GTA 6 MARATHON | Kai Cenat", "uploader": "Kai Cenat",
                            "url": "https://youtube.com/watch?v=x"},
            "llm": {"caption": "Would you have jumped?", "description": "Kai Cenat crashes a plane in GTA 6.",
                    "search_phrases": ["kai cenat gta 6", "gta 6 funny moments", "gta 6 plane crash", "kai cenat"],
                    "hashtags": ["#GTA6", "#KaiCenat"]}}
    pkg = pipeline.post_package("Kai's GTA 6 Plane Crash", meta)
    d = pkg["youtube_description"]
    assert d.startswith("Would you have jumped?")
    assert "Kai Cenat crashes a plane" in d and "kai cenat gta 6" in d and "Original content: Kai Cenat" in d
    assert 10 <= d.count("#") <= 25 and len(d) <= 4900
    assert sum(len(t) + 3 for t in pkg["youtube_tags"]) <= 500 and "kai cenat gta 6" in pkg["youtube_tags"]
    assert pkg["instagram"].count("#") == 5
    assert pkg["tiktok"].count("#") <= 8


def test_plan_edit_tease_body_replay():
    spans, info = pipeline.plan_edit([], 30.0, None, payoff=20.0, tease=True, replay=True)
    assert spans[0] == (18.8, 20.8, 1.0)  # the payoff first
    assert spans[1] == (0.0, 30.0, 1.0)  # then the whole clip
    assert spans[2][:2] == (18.0, 21.0) and spans[2][2] < 1  # slow-mo replay last, loops into the tease
    assert info["tease"] and info["replay"]
    # payoff right at the start: no tease (it is already the opening)
    spans, info = pipeline.plan_edit([], 30.0, None, payoff=2.0, tease=True, replay=False)
    assert spans == [(0.0, 30.0, 1.0)] and not info["tease"]


def test_words_follow_tease_and_replay():
    words = [transcriber.Word(19.0, 19.5, "NO"), transcriber.Word(5.0, 5.4, "hey")]
    spans = [(18.8, 20.8, 1.0), (0.0, 30.0, 1.0), (18.0, 21.0, 0.7)]
    out = pipeline.remap_words_spans(words, spans)
    starts = [round(w.start, 2) for w in out if w.text == "NO"]
    assert len(starts) == 3 and abs(starts[0] - 0.2) < 1e-6 and abs(starts[1] - 21.0) < 1e-6


def test_builtin_beat_is_generated(tmp_path):
    p = music.pick_track(tmp_path, "abc")
    assert p.exists() and p.stat().st_size > 500_000


def test_user_track_is_preferred(tmp_path):
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "my_cc0_track.mp3").write_bytes(b"x")
    assert music.pick_track(tmp_path, "abc").name == "my_cc0_track.mp3"


def test_render_with_tease_replay_beat_and_progress(sample_video, monkeypatch):
    monkeypatch.setattr(transcriber, "transcribe_words", lambda *a, **k: [])
    job = pipeline.submit("upload", "gameplay.mp4", raw_path=sample_video,
                          options={"clips": 1, "clip_seconds": 20})
    src = get_store().get(job)
    steps = {s["key"]: s["state"] for s in src["meta"]["progress"]["stages"]}
    assert steps["download"] == "skipped" and steps["analyze"] == "done" and steps["clips"] == "done"
    c = get_store().children(job)[0]
    m = c["meta"]
    assert c["status"] == "awaiting_approval"
    states = {s["key"]: s for s in m["progress"]["stages"]}
    assert all(states[k]["state"] in ("done", "warn") for k in ("transcribe", "ai", "plan", "cover", "render", "finish"))
    assert states["render"]["pct"] == 100.0
    assert m["payoff_s"] is not None and m["replay"] and m["beat_track"]
    body = float(c["end_s"]) - float(c["start_s"])
    out = probe(Path(c["output_path"]))
    assert out.has_audio and out.duration > body + 2  # tease + replay were added


def test_publish_progress_and_visibility_warning(sample_video, monkeypatch, tmp_path):
    from app.services import social_publisher as sp

    monkeypatch.setattr(transcriber, "transcribe_words", lambda *a, **k: [])
    job = pipeline.submit("upload", "gameplay.mp4", raw_path=sample_video,
                          options={"clips": 1, "clip_seconds": 15, "tease": False, "replay": False, "beat": False})
    cid = get_store().children(job)[0]["id"]
    monkeypatch.setattr(sp, "publishers_configured", lambda: {"youtube": True})
    seen = {}

    def fake_upload(video, title, description, tags=None, on_step=None, **k):
        on_step("upload", pct=50)
        on_step("upload", "done")
        on_step("meta", "done")
        on_step("thumb", "done")
        seen["description"], seen["tags"] = description, tags
        return "vid9"
    monkeypatch.setattr(sp, "upload_youtube", fake_upload)
    monkeypatch.setattr(sp, "youtube_status", lambda ids: {"vid9": {"privacy": "private", "views": 0}})
    assert pipeline.approve(cid) == "Approved, publishing now"
    c = get_store().get(cid)
    assert c["status"] == "published" and c["meta"]["youtube_id"] == "vid9"
    st = {s["key"]: s for s in c["meta"]["publish_progress"]["stages"]}
    assert st["youtube.upload"]["state"] == "done" and st["youtube.visibility"]["state"] == "warn"
    assert "audit" in st["youtube.visibility"]["detail"] and st["youtube.link"]["state"] == "done"
    assert seen["tags"] and seen["description"].count("#") >= 8
    # fixing visibility afterwards
    monkeypatch.setattr(sp, "set_youtube_privacy", lambda vid, p="public": {"privacy": "public"})
    res = pipeline.make_youtube_public()
    assert res[0]["ok"] and get_store().get(cid)["meta"]["youtube_privacy"] == "public"


def test_ffmpeg_progress_callback(sample_video, tmp_path):
    pcts = []
    video_editor.render_clip(sample_video, tmp_path / "o.mp4", 10, 16, out_w=540, out_h=960,
                             keep=[(3, 4), (0, 6), (2, 5, 0.7)], on_progress=pcts.append,
                             music=music.pick_track(tmp_path, "x"))
    assert pcts and pcts[-1] == 100.0
    info = probe(tmp_path / "o.mp4")
    assert abs(info.duration - (1 + 6 + 3 / 0.7)) < 0.6
