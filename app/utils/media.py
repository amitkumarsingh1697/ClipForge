"""Thin, logged wrappers around ffprobe/ffmpeg subprocess calls."""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> str:
    log.debug("run: %s", " ".join(map(str, cmd)))
    try:
        p = subprocess.run(list(map(str, cmd)), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:  # a stuck ffmpeg must fail the job, not block the queue forever
        raise MediaError(f"{cmd[0]} timed out after {timeout:.0f}s") from e
    if p.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-1500:]}")
    return p.stdout


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    has_audio: bool
    fps: float

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def probe(path: Path) -> MediaInfo:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise MediaError(f"{path} has no video stream")
    w, h = int(v["width"]), int(v["height"])
    # Phone recordings carry rotation metadata; ffmpeg auto-rotates on decode,
    # so report the *displayed* size.
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(v.get("tags", {}).get("rotate", rot) or rot)
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(data["format"].get("duration") or v.get("duration") or 0)
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
    return MediaInfo(duration=duration, width=w, height=h, has_audio=has_audio, fps=fps or 30.0)


def extract_audio(src: Path, dst: Path, start: float = 0.0, duration: float | None = None,
                  sr: int = 22050, speech_filter: bool = False) -> Path:
    """Decode audio to mono WAV. speech_filter band-limits to the voice range and
    denoises, which keeps Whisper from hallucinating words out of gunfire/music."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", src, "-vn", "-ac", "1", "-ar", str(sr)]
    if speech_filter:
        cmd += ["-af", "highpass=f=120,lowpass=f=4000,afftdn=nf=-25,dynaudnorm"]
    cmd += [dst]
    run(cmd)
    return dst
