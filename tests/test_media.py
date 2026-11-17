from pathlib import Path

from app.services import highlight_ai, transcriber, video_editor
from app.utils.media import probe


def test_highlight_finds_loud_burst(sample_video, tmp_path):
    hl = highlight_ai.find_highlights(sample_video, tmp_path, target_s=30, min_s=15, max_s=60, top_n=1)
    assert len(hl) == 1
    h = hl[0]
    assert 15 <= h.duration <= 60
    assert h.start <= 52 and h.end >= 58, h  # covers the 50-60 s burst


def test_highlight_multiple_non_overlapping(sample_video, tmp_path):
    hl = highlight_ai.find_highlights(sample_video, tmp_path, target_s=20, min_s=15, max_s=25, top_n=3)
    assert len(hl) == 3
    for a, b in zip(hl, hl[1:]):
        assert a.end <= b.start + 1e-6


def test_short_clip_kept_whole(short_portrait, tmp_path):
    hl = highlight_ai.find_highlights(short_portrait, tmp_path)
    assert hl[0].start == 0 and abs(hl[0].end - probe(short_portrait).duration) < 0.1


def test_spec_crop_width_is_odd_and_fixed():
    info = probe.__globals__["MediaInfo"](duration=1, width=1920, height=1080, has_audio=True, fps=30)
    assert 1080 * 9 / 16 == 607.5  # the spec's crop=ih*9/16 -> non-integer/odd width
    assert video_editor.crop_width(info) == 606


def test_smart_crop_follows_action(sample_video):
    info = probe(sample_video)
    x = video_editor.find_crop_x(sample_video, 0, 20, info)
    cw = video_editor.crop_width(info)
    centre = (info.width - cw) // 2
    assert x > centre + 100, (x, centre)      # moved right, toward the box
    assert x <= 960 <= x + cw                  # box (x~800-1120) is inside the crop
    assert x % 2 == 0


def test_render_landscape_with_captions(sample_video, tmp_path):
    words = [transcriber.Word(0.5, 0.9, "no"), transcriber.Word(0.9, 1.3, "way"),
             transcriber.Word(1.3, 1.8, "bro"), transcriber.Word(2.0, 2.6, "{weird}")]
    ass = transcriber.write_ass(words, tmp_path / "c.ass")
    assert "NO WAY BRO" in ass.read_text() and "{weird}" not in ass.read_text()
    out = video_editor.render_vertical(sample_video, tmp_path / "out dir with space.mp4", 45, 65, ass_file=ass)
    i = probe(out)
    assert (i.width, i.height) == (1080, 1920)
    assert abs(i.duration - 20) < 0.5 and i.has_audio
    assert out.stat().st_size < 48 * 1024 * 1024


def test_render_portrait_source_pads(short_portrait, tmp_path):
    out = video_editor.render_vertical(short_portrait, tmp_path / "p.mp4", 0, 12)
    i = probe(out)
    assert (i.width, i.height) == (1080, 1920)


def test_whisper_filter_drops_hallucinations():
    class W:  # minimal faster-whisper shapes
        def __init__(self, s, e, w, p=0.9):
            self.start, self.end, self.word, self.probability = s, e, w, p

    class S:
        def __init__(self, text, nsp, lp, words):
            self.text, self.no_speech_prob, self.avg_logprob, self.words = text, nsp, lp, words

    segs = [S(" Thanks for watching!", 0.1, -0.3, [W(0, 1, "Thanks")]),
            S(" boom boom", 0.8, -0.2, [W(1, 2, "boom")]),
            S(" get in the car", 0.1, -0.4, [W(2, 2.3, " get"), W(2.3, 2.5, " in", 0.1), W(2.5, 3, " car")])]
    words = transcriber.filter_words(segs)
    assert [w.text for w in words] == ["get", "car"]
    assert transcriber.write_ass([], Path("/nonexistent.ass")) is None


import pytest


@pytest.mark.parametrize("fmt", ["portrait", "square", "landscape"])
def test_render_other_formats_from_landscape(sample_video, tmp_path, fmt):
    w, h = video_editor.format_size(fmt)
    ass = transcriber.write_ass([transcriber.Word(0.2, 0.8, "hi")], tmp_path / "c.ass", w, h)
    out = video_editor.render_clip(sample_video, tmp_path / f"{fmt}.mp4", 50, 56, ass_file=ass, out_w=w, out_h=h)
    i = probe(out)
    assert (i.width, i.height) == (w, h)


def test_vertical_source_to_landscape_and_portrait(short_portrait, tmp_path):
    info = probe(short_portrait)
    assert "split=2" in video_editor.frame_filter(info, 0, 1920, 1080)        # much wider: blur-fit
    assert video_editor.frame_filter(info, 0, 1080, 1350).startswith("crop=720:900")  # 9:16 -> 4:5: trim top/bottom
    out = video_editor.render_clip(short_portrait, tmp_path / "l.mp4", 0, 5, out_w=1920, out_h=1080)
    assert (probe(out).width, probe(out).height) == (1920, 1080)


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        video_editor.format_size("diagonal")


def test_render_without_libass_skips_captions(sample_video, tmp_path, monkeypatch):
    monkeypatch.setattr(video_editor, "can_burn_captions", lambda: False)
    ass = transcriber.write_ass([transcriber.Word(0.2, 0.8, "hi")], tmp_path / "c.ass")
    out = video_editor.render_clip(sample_video, tmp_path / "noass.mp4", 50, 55, ass_file=ass)
    assert probe(out).height == 1920


def test_has_filter_detects_real_filters():
    assert video_editor.has_filter("scale") and not video_editor.has_filter("no_such_filter_xyz")
