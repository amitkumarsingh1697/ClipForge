"""faster-whisper implementation + caption file generation.

FIXES vs the spec:
  * Gameplay audio is mostly gunfire, engines and music. Plain Whisper on that audio
    hallucinates captions ("Thanks for watching!"). Audio is band-passed to the
    voice range and denoised first, Whisper's VAD filter is on, and segments
    with high no_speech probability or low confidence are dropped. A clip with
    no real speech simply gets no captions instead of nonsense.
  * The spec generated a sentence-level .srt, which renders as small 2-line
    blocks. Shorts captions are short, big, centred word groups, so word
    timestamps are used to write an .ass file with 1080x1920 styling placed
    above the Shorts/Reels UI.
  * The model is loaded once per worker process, not once per job.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.utils.media import extract_audio

log = logging.getLogger(__name__)

HALLUCINATIONS = {
    "thanks for watching", "thank you for watching", "please subscribe", "subscribe",
    "thank you", "you", "bye", "music",
}


@dataclass
class Word:
    start: float
    end: float
    text: str


@lru_cache(maxsize=1)
def _model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    log.info("loading whisper model %s on %s", name, device)
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe_words(video: Path, work_dir: Path, start: float, duration: float, model: str = "small",
                     device: str = "auto", compute_type: str = "int8", language: str | None = None) -> list[Word]:
    wav = extract_audio(video, work_dir / f"{video.stem}_{int(start*1000)}_speech.wav", start=start,
                        duration=duration, sr=16000, speech_filter=True)
    try:
        segments, _info = _model(model, device, compute_type).transcribe(
            str(wav), language=language, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            word_timestamps=True, condition_on_previous_text=False, beam_size=5,
        )
        return filter_words(segments)
    finally:
        wav.unlink(missing_ok=True)


def filter_words(segments) -> list[Word]:
    words: list[Word] = []
    for seg in segments:
        text = (seg.text or "").strip().lower().strip(".!? ")
        if seg.no_speech_prob > 0.6 or seg.avg_logprob < -1.0 or text in HALLUCINATIONS:
            continue
        for w in seg.words or []:
            if getattr(w, "probability", 1.0) < 0.3 or not w.word.strip():
                continue
            words.append(Word(float(w.start), float(w.end), w.word.strip()))
    return words


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


CAPTION_STYLES = ("pop", "classic", "minimal", "none")


def _bgr(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _wrap_hook(text: str, max_chars: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:3])


def write_ass(words: list[Word], dst: Path, width: int = 1080, height: int = 1920,
              words_per_line: int = 3, font: str = "DejaVu Sans", style: str = "pop",
              hook: str | None = None, hook_seconds: float = 3.0,
              keywords: set[str] | None = None, highlight: str = "#FFE600",
              keyword_color: str = "#3DFF7A", animate_hook: bool = True,
              hook_scale: float = 1.0) -> Path | None:
    """Short-form captions + optional hook title. Returns None if there is nothing to draw.

    Styles (what viral clip tools use):
      pop      word-by-word: the group stays on screen, the spoken word turns
               yellow and pops; keywords are green (OpusClip/Submagic look)
      classic  big white word groups with a thick outline
      minimal  smaller sentence-case text on a dark box
      none     no captions (hook title still drawn if given)
    Vertical clips keep captions above the Shorts/Reels buttons; other shapes sit near the bottom.
    """
    style = style if style in CAPTION_STYLES else "pop"
    if style == "none":
        words = []
    if not words and not hook:
        return None
    vertical = height > width * 1.5
    base = int(min(width, height) * (0.065 if style == "minimal" else 0.09))
    margin_v = int(height * (0.28 if vertical else 0.10))
    hook_size = int(min(width, height) * 0.08 * hook_scale)
    hl, kw = _bgr(highlight), _bgr(keyword_color)
    keywords = {k.lower().strip(".,!?") for k in (keywords or set())}
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{base},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,{margin_v},1
Style: Min,{font},{base},&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,3,14,0,2,80,80,{margin_v},1
Style: Hook,{font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,22,0,8,70,70,{int(height * (0.12 if vertical else 0.06))},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        h = _wrap_hook(_esc(hook), int((18 if vertical else 32) / max(hook_scale, 0.5)) + 2)
        anim = "{\\fad(150,250)\\t(0,200,\\fscx105\\fscy105)\\t(200,400,\\fscx100\\fscy100)}" if animate_hook else ""
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_seconds)},Hook,,0,0,0,,{anim}{h}")
    for i in range(0, len(words), words_per_line):
        group = words[i:i + words_per_line]
        g_start = group[0].start
        g_next = words[i + words_per_line].start if i + words_per_line < len(words) else group[-1].end + 0.4
        g_end = min(max(group[-1].end, g_start + 0.4), g_next)
        if style == "minimal":
            text = " ".join(_esc(w.text) for w in group)
            lines.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Min,,0,0,0,,{text}")
            continue
        if style == "classic":
            text = " ".join(_esc(w.text) for w in group).upper()
            lines.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{{\\fad(60,60)}}{text}")
            continue
        # pop: one event per spoken word, the group visible throughout
        for j, w in enumerate(group):
            w_start = g_start if j == 0 else w.start
            w_end = group[j + 1].start if j + 1 < len(group) else g_end
            if w_end <= w_start:
                continue
            parts = []
            for k, other in enumerate(group):
                t = _esc(other.text).upper()
                is_kw = other.text.lower().strip(".,!?") in keywords
                if k == j:
                    parts.append(f"{{\\c{hl}&\\t(0,90,\\fscx118\\fscy118)\\t(90,180,\\fscx100\\fscy100)}}{t}"
                                 f"{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                elif is_kw:
                    parts.append(f"{{\\c{kw}&}}{t}{{\\c&H00FFFFFF&}}")
                else:
                    parts.append(t)
            lines.append(f"Dialogue: 0,{_ts(w_start)},{_ts(w_end)},Cap,,0,0,0,,{' '.join(parts)}")
    dst.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return dst
