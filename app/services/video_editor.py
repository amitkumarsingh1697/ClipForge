"""FFmpeg cropping and clipping logic.

FIXES vs the spec's `-vf "crop=ih*9/16:ih"`:
  1. ih*9/16 is usually odd (1080*9/16 = 607.5) and libx264 rejects odd widths
     with yuv420p, so the render fails. Width is rounded down to an even number.
  2. That filter always crops the centre. the action in gameplay, sports or
     streams is often off-centre, so crop_x is chosen from where motion actually is.
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
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.utils.media import MediaInfo, probe, run

log = logging.getLogger(__name__)
TARGET_ASPECT = 9 / 16

# Output formats the user can pick per job.
FORMATS: dict[str, tuple[int, int]] = {
    "vertical": (1080, 1920),   # 9:16  Shorts / Reels / TikTok
    "portrait": (1080, 1350),   # 4:5   Instagram / Facebook feed
    "square": (1080, 1080),     # 1:1
    "landscape": (1920, 1080),  # 16:9  YouTube / X
}


def format_size(name: str) -> tuple[int, int]:
    if name not in FORMATS:
        raise ValueError(f"unknown format {name!r}; choose from {', '.join(FORMATS)}")
    return FORMATS[name]


@lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    """Some ffmpeg builds (e.g. Homebrew's default ffmpeg 8+) ship without libass."""
    try:
        out = run(["ffmpeg", "-hide_banner", "-filters"])
    except Exception:
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def can_burn_captions() -> bool:
    return has_filter("ass")


def even(x: float) -> int:
    return int(x) // 2 * 2


def crop_width(info: MediaInfo, target_aspect: float = TARGET_ASPECT) -> int:
    return min(even(info.height * target_aspect), even(info.width))


def find_crop_x(video: Path, start: float, duration: float, info: MediaInfo,
                samples: int = 24, target_aspect: float = TARGET_ASPECT) -> int:
    """Pick the horizontal crop offset that keeps the most on-screen motion.

    Motion energy per column is summed over sampled frame pairs, multiplied by
    a mild centre prior (so static scenes stay centred, and the HUD/minimap at
    the edges doesn't win), then the best crop-width window is chosen.
    """
    import cv2

    cw = crop_width(info, target_aspect)
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


def frame_filter(info: MediaInfo, crop_x: int, out_w: int, out_h: int) -> str:
    """Filter that turns any source shape into out_w x out_h.

    * source wider than target  -> crop the sides at crop_x (smart position)
    * source a bit taller       -> crop top/bottom evenly (e.g. 9:16 -> 4:5)
    * source much taller        -> fit over a blurred copy (e.g. 9:16 -> 16:9)
    """
    target = out_w / out_h
    if info.aspect > target * 1.01:
        cw = crop_width(info, target)
        return f"crop={cw}:{even(info.height)}:{crop_x}:0,scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    if info.aspect >= target * 0.7:
        ch = min(even(info.width / target), even(info.height))
        return f"crop={even(info.width)}:{ch}:0:(ih-{ch})/2,scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=20:2[bgb];"
        f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


vertical_filter = frame_filter  # backwards-compatible name


def render_clip(src: Path, dst: Path, start: float, end: float, ass_file: Path | None = None,
                out_w: int = 1080, out_h: int = 1920, max_mb: float | None = None,
                crop_x: int | None = None, progress_bar: bool = False,
                bar_color: str = "FFE600", max_kbps: int = 8000) -> Path:
    """Render the final clip. max_mb (optional) caps the file size; otherwise
    quality is capped at max_kbps (long clips are not squeezed into 50 MB:
    Telegram gets a separate preview, see make_preview)."""
    info = probe(src)
    duration = max(0.5, min(end, info.duration) - start)
    target = out_w / out_h
    if crop_x is None:
        crop_x = find_crop_x(src, start, duration, info, target_aspect=target) if info.aspect > target * 1.01 else 0
    vf = frame_filter(info, crop_x, out_w, out_h)
    cwd = None
    if ass_file is not None and not can_burn_captions():
        log.warning("ffmpeg has no 'ass' filter (built without libass); rendering %s without captions", dst.name)
        ass_file = None
    if ass_file is not None:
        # Run ffmpeg *in* the subtitle's folder and pass a bare file name: this
        # sidesteps the filtergraph escaping of ':' '\' and spaces in paths,
        # a classic cause of "Unable to open subtitles" failures.
        cwd = ass_file.parent
        vf += f",ass={ass_file.name}"
    graph = f"[0:v]{vf}[v0]"
    out_label = "[v0]"
    if progress_bar:
        bar_h = max(12, min(out_w, out_h) // 54)  # ~20 px on 1080 wide: visible on phones
        graph += (f";color=c=0x{bar_color}:s={out_w}x{bar_h}:r=30[bar];"
                  f"[v0][bar]overlay=x='-w+w*t/{duration:.3f}':y=H-h:shortest=1:eval=frame[v1]")
        out_label = "[v1]"
    audio_kbps = 128
    vmax = max_kbps
    if max_mb:
        vmax = max(1500, min(max_kbps, int(max_mb * 8 * 1024 / duration) - audio_kbps))
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src.resolve(),
        "-filter_complex", graph, "-map", out_label, "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-maxrate", f"{vmax}k", "-bufsize", f"{vmax * 2}k", "-r", "30",
        "-movflags", "+faststart", "-t", f"{duration:.3f}",
    ]
    if info.has_audio:
        cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += [dst.resolve()]
    run(cmd, cwd=cwd)
    size_mb = dst.stat().st_size / 1024 / 1024
    log.info("rendered %s (%.1fs, %.1f MB, crop_x=%s)", dst.name, duration, size_mb, crop_x)
    return dst


def make_preview(src: Path, dst: Path, max_mb: float = 45.0) -> Path:
    """Smaller copy for Telegram (bots can upload 50 MB max) when the real clip is bigger."""
    info = probe(src)
    kbps = max(300, int(max_mb * 8 * 1024 / max(info.duration, 1)) - 96)
    w = 720 if info.width <= info.height else 1280
    run(["ffmpeg", "-y", "-v", "error", "-i", src.resolve(), "-vf", f"scale={w}:-2",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
         "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart", dst.resolve()])
    return dst


def pick_cover_time(src: Path, start: float, end: float, samples: int = 12) -> float:
    """Sharpest, busiest frame in the clip (skips blurry motion frames and black frames)."""
    import cv2

    cap = cv2.VideoCapture(str(src))
    best_t, best = start + (end - start) * 0.3, -1.0
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(f, (480, int(480 * f.shape[0] / f.shape[1]))), cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(g, cv2.CV_64F).var()
        bright = g.mean()
        score = sharp * (0.3 if bright < 25 or bright > 235 else 1.0)
        if score > best:
            best, best_t = score, t
    cap.release()
    return best_t


def make_cover(src: Path, dst: Path, t: float, out_w: int, out_h: int, crop_x: int | None = None,
               ass_file: Path | None = None) -> Path:
    """Cover/thumbnail image: the chosen frame, reframed like the clip, with the hook title on it."""
    info = probe(src)
    target = out_w / out_h
    if crop_x is None:
        crop_x = find_crop_x(src, max(0, t - 1), 2, info, target_aspect=target) if info.aspect > target * 1.01 else 0
    vf = frame_filter(info, crop_x, out_w, out_h)
    cwd = None
    if ass_file is not None and can_burn_captions():
        cwd = ass_file.parent
        vf += f",ass={ass_file.name}"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", src.resolve(), "-filter_complex",
         f"[0:v]{vf}[v]", "-map", "[v]", "-frames:v", "1", "-q:v", "3", dst.resolve()], cwd=cwd)
    return dst


render_vertical = render_clip  # backwards-compatible name
