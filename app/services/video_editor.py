"""FFmpeg cropping and clipping logic.

FIXES vs the spec's `-vf "crop=ih*9/16:ih"`:
  1. ih*9/16 is usually odd (1080*9/16 = 607.5) and libx264 rejects odd widths
     with yuv420p, so the render fails. Width is rounded down to an even number.
  2. That filter always crops the centre. GTA action (car chases, shootouts) is
     often off-centre, so crop_x is chosen from where motion actually is.
  3. Output was never scaled, so a 720p source produced a 405x720 "Short".
     Everything is scaled to 1080x1920.
  4. A phone that records the game *vertically* (or a vertical source URL)
     has iw < ih*9/16 and the crop filter errors out. Portrait/square sources
     are scaled and padded with a blurred copy instead.
  5. Output is H.264 yuv420p + AAC, loudness-normalised to -14 LUFS,
     +faststart, and capped in bitrate so it fits Telegram's 50 MB bot limit.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from app.utils.media import MediaInfo, probe, run

log = logging.getLogger(__name__)
TARGET_ASPECT = 9 / 16


def even(x: float) -> int:
    return int(x) // 2 * 2


def crop_width(info: MediaInfo) -> int:
    return min(even(info.height * TARGET_ASPECT), even(info.width))


def find_crop_x(video: Path, start: float, duration: float, info: MediaInfo,
                samples: int = 24) -> int:
    """Pick the horizontal crop offset that keeps the most on-screen motion.

    Motion energy per column is summed over sampled frame pairs, multiplied by
    a mild centre prior (so static scenes stay centred, and the HUD/minimap at
    the edges doesn't win), then the best crop-width window is chosen.
    """
    import cv2

    cw = crop_width(info)
    if cw >= info.width:
        return 0
    centre = even((info.width - cw) / 2)
    cap = cv2.VideoCapture(str(video))
    small_w = 320
    scale = small_w / info.width
    energy = np.zeros(small_w, dtype=np.float64)
    for k in range(samples):
        t = start + duration * (k + 0.5) / samples
        frames = []
        for dt in (0.0, 0.2):
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + dt) * 1000)
            ok, f = cap.read()
            if not ok:
                break
            h = max(1, int(f.shape[0] * small_w / f.shape[1]))
            frames.append(cv2.cvtColor(cv2.resize(f, (small_w, h)), cv2.COLOR_BGR2GRAY).astype(np.float32))
        if len(frames) == 2:
            energy += np.abs(frames[1] - frames[0]).sum(axis=0)
    cap.release()
    if energy.sum() <= 0:
        return centre
    xs = np.arange(small_w)
    prior = np.exp(-0.5 * ((xs - small_w / 2) / (small_w * 0.35)) ** 2)
    energy = energy * (0.5 + 0.5 * prior)
    win = max(1, int(round(cw * scale)))
    csum = np.concatenate([[0.0], np.cumsum(energy)])
    sums = csum[win:] - csum[:-win]
    best = int(np.argmax(sums))
    # Only move off-centre if it is clearly better (avoids jitter on noise).
    centre_small = int(round(centre * scale))
    if sums[best] < 1.1 * sums[min(centre_small, len(sums) - 1)]:
        return centre
    x = even(best / scale)
    return max(0, min(x, even(info.width - cw)))


def vertical_filter(info: MediaInfo, crop_x: int, out_w: int, out_h: int) -> str:
    if info.aspect > TARGET_ASPECT + 0.01:
        cw = crop_width(info)
        return f"crop={cw}:{even(info.height)}:{crop_x}:0,scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    # Portrait / square: fit inside 9:16 over a blurred, zoomed background.
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=20:2[bgb];"
        f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def render_vertical(src: Path, dst: Path, start: float, end: float, ass_file: Path | None = None,
                    out_w: int = 1080, out_h: int = 1920, max_mb: float = 48.0,
                    crop_x: int | None = None) -> Path:
    info = probe(src)
    duration = max(0.5, min(end, info.duration) - start)
    if crop_x is None:
        crop_x = find_crop_x(src, start, duration, info) if info.aspect > TARGET_ASPECT + 0.01 else 0
    vf = vertical_filter(info, crop_x, out_w, out_h)
    cwd = None
    if ass_file is not None:
        # Run ffmpeg *in* the subtitle's folder and pass a bare file name: this
        # sidesteps the filtergraph escaping of ':' '\' and spaces in paths,
        # a classic cause of "Unable to open subtitles" failures.
        cwd = ass_file.parent
        vf += f",ass={ass_file.name}"
    # Bitrate budget: keep (video+audio) under max_mb.
    audio_kbps = 128
    budget_kbps = int(max_mb * 8 * 1024 / duration) - audio_kbps
    vmax = max(1500, min(8000, budget_kbps))
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src.resolve(),
        "-filter_complex" if ";" in vf else "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
        "-maxrate", f"{vmax}k", "-bufsize", f"{vmax * 2}k", "-r", "30",
        "-movflags", "+faststart",
    ]
    if info.has_audio:
        cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    else:
        cmd += ["-an"]
    cmd += [dst.resolve()]
    run(cmd, cwd=cwd)
    size_mb = dst.stat().st_size / 1024 / 1024
    log.info("rendered %s (%.1fs, %.1f MB, crop_x=%s)", dst.name, duration, size_mb, crop_x)
    return dst
