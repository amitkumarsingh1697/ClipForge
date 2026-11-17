"""faster-whisper implementation + caption file generation.

FIXES vs the spec:
  * GTA audio is mostly gunfire, engines and music. Plain Whisper on that audio
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


def write_ass(words: list[Word], dst: Path, width: int = 1080, height: int = 1920,
              words_per_line: int = 3, font: str = "DejaVu Sans") -> Path | None:
    """Big bold word-group captions. Returns None if there is nothing to show."""
    if not words:
        return None
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{int(height*0.045)},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,{int(height*0.28)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i in range(0, len(words), words_per_line):
        group = words[i:i + words_per_line]
        start = group[0].start
        nxt = words[i + words_per_line].start if i + words_per_line < len(words) else group[-1].end + 0.4
        end = min(max(group[-1].end, start + 0.4), nxt)
        text = " ".join(_esc(w.text) for w in group).upper()
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{{\\fad(60,60)}}{text}")
    dst.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return dst
