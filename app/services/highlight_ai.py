"""Audio/visual peak detection for cuts.

FIX: the spec said "isolate the best 15-60 second chunk" from volume spikes but
never defined the window, what happens with clips shorter than 15 s, or how a
long VOD / stream yields more than one clip. This module:
  * scores every 0.5 s hop by loudness (RMS dB), onset strength (gunshots,
    impacts) and optionally visual motion,
  * slides a window of the target length and takes the top-N non-overlapping
    windows,
  * snaps window edges to nearby quiet moments so a cut doesn't land mid-word,
  * returns the whole clip when it is already short enough.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.utils.media import extract_audio, probe

log = logging.getLogger(__name__)
HOP_S = 0.5


@dataclass
class Highlight:
    start: float
    end: float
    score: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _z(x: np.ndarray) -> np.ndarray:
    sd = float(x.std())
    return (x - float(x.mean())) / sd if sd > 1e-9 else np.zeros_like(x)


def audio_curve(wav: Path, sr: int = 22050) -> np.ndarray:
    """Excitement score per HOP_S of audio (z-scored, smoothed)."""
    import librosa

    y, sr = librosa.load(str(wav), sr=sr, mono=True)
    if y.size == 0:
        return np.zeros(1)
    hop = int(sr * HOP_S)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-10, ref=np.max)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    n = min(len(rms_db), len(onset))
    score = 0.6 * _z(rms_db[:n]) + 0.4 * _z(onset[:n])
    k = 3  # 1.5 s smoothing
    return np.convolve(score, np.ones(k) / k, mode="same")


def motion_curve(video: Path, n_hops: int) -> np.ndarray:
    """Mean frame-difference per hop (cheap proxy for on-screen action)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps * HOP_S)))
    vals, prev, i = [], None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            vals.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
            prev = g
        i += 1
    cap.release()
    arr = np.array(vals or [0.0])
    out = np.zeros(n_hops)
    m = min(n_hops, len(arr))
    out[:m] = arr[:m]
    return _z(out)


def select_windows(curve: np.ndarray, duration: float, target_s: float, min_s: float,
                   max_s: float, top_n: int) -> list[Highlight]:
    if duration <= max_s:
        # Short input: keep it whole (no 15 s floor violation, no empty window).
        return [Highlight(0.0, duration, float(curve.mean()) if curve.size else 0.0)]
    win = max(1, int(round(min(max(target_s, min_s), max_s) / HOP_S)))
    if len(curve) <= win:
        return [Highlight(0.0, min(duration, max_s), float(curve.mean()))]
    csum = np.concatenate([[0.0], np.cumsum(curve)])
    means = (csum[win:] - csum[:-win]) / win  # mean score of window starting at hop i
    order = np.argsort(means)[::-1]
    taken: list[tuple[int, int]] = []
    picks: list[Highlight] = []
    for i in order:
        i = int(i)
        if any(not (i + win <= a or i >= b) for a, b in taken):
            continue
        taken.append((i, i + win))
        start = i * HOP_S
        picks.append(Highlight(start, min(duration, start + win * HOP_S), float(means[i])))
        if len(picks) >= top_n:
            break
    return [_snap(h, curve, duration, min_s, max_s) for h in sorted(picks, key=lambda h: h.start)]


def _snap(h: Highlight, curve: np.ndarray, duration: float, min_s: float, max_s: float,
          reach_s: float = 1.5) -> Highlight:
    """Move each edge to the quietest hop within +-reach_s (avoid cutting mid-action/word)."""
    r = int(reach_s / HOP_S)

    def quiet(t: float) -> float:
        c = int(t / HOP_S)
        lo, hi = max(0, c - r), min(len(curve), c + r + 1)
        if hi <= lo:
            return t
        return (lo + int(np.argmin(curve[lo:hi]))) * HOP_S

    s, e = quiet(h.start), quiet(h.end)
    s, e = max(0.0, s), min(duration, e)
    if not (min_s <= e - s <= max_s):
        s, e = h.start, h.end
    return Highlight(round(s, 2), round(e, 2), h.score)


def find_highlights(video: Path, work_dir: Path, target_s: float = 30, min_s: float = 15,
                    max_s: float = 60, top_n: int = 1, use_motion: bool = True) -> list[Highlight]:
    info = probe(video)
    if info.duration <= max_s:
        return [Highlight(0.0, info.duration, 0.0)]
    if info.has_audio:
        wav = extract_audio(video, work_dir / f"{video.stem}_hl.wav")
        curve = audio_curve(wav)
        wav.unlink(missing_ok=True)
    else:
        curve = np.zeros(int(info.duration / HOP_S) + 1)
    if use_motion:
        curve = 0.75 * curve + 0.25 * motion_curve(video, len(curve))
    hl = select_windows(curve, info.duration, target_s, min_s, max_s, top_n)
    log.info("highlights for %s: %s", video.name, hl)
    return hl


def peak_score(video: Path, work_dir: Path) -> float:
    """Single excitement number for a live segment (95th percentile of the audio curve, un-normalised)."""
    import librosa

    wav = extract_audio(video, work_dir / f"{video.stem}_peak.wav")
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    wav.unlink(missing_ok=True)
    if y.size == 0:
        return 0.0
    rms = librosa.feature.rms(y=y, hop_length=int(sr * HOP_S))[0]
    return float(np.percentile(rms, 95))
