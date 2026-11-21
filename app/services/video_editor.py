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
    faces = np.zeros(small_w, dtype=np.float64)
    detector = _face_detector()
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
        if frames and detector is not None and k % 2 == 0:
            g = frames[0].astype(np.uint8)
            for (fx, fy, fw, fh) in detector.detectMultiScale(g, scaleFactor=1.15, minNeighbors=5,
                                                               minSize=(max(12, small_w // 25),) * 2):
                faces[fx:fx + fw] += fw * fh
    cap.release()
    if faces.sum() > 0:
        # Faces (streamer cam, podcast guests, reactions) matter more than raw motion.
        energy = energy / (energy.sum() or 1.0) + 2.0 * faces / faces.sum()
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


@lru_cache(maxsize=1)
def _face_detector():
    try:
        import cv2

        d = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        return None if d.empty() else d
    except Exception:  # no cascade files in this OpenCV build
        return None


def zoom_filter(times: list, out_w: int, out_h: int, amount: float = 0.12) -> str:
    """Punch-in zoom during the given (start, end[, amount]) output times: the frame jumps in and back.
    A third value sets a stronger zoom for that window (used for the replay of the payoff)."""
    groups: dict[float, list[str]] = {}
    for t in times:
        a, b = t[0], t[1]
        amt = t[2] if len(t) > 2 else amount
        groups.setdefault(round(1 + amt, 3), []).append(f"between(it,{a:.3f},{b:.3f})")
    z = "1"
    for level, conds in sorted(groups.items()):
        z = f"if({'+'.join(conds)},{level:.3f},{z})"
    return (f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={out_w}x{out_h}:fps=30,setsar=1")


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
                bar_color: str = "FFE600", max_kbps: int = 8000,
                keep: list[tuple[float, float]] | None = None,
                zoom_times: list | None = None,
                intro_image: Path | None = None, intro_s: float = 0.25,
                music: Path | None = None, music_volume: float = 0.12,
                on_progress=None) -> Path:
    """Render the final clip.

    keep        the (start, end[, speed]) spans, relative to the clip, played in this order.
                Used for jump cuts (dead air removed), the payoff tease at the start and
                the slow-motion replay at the end. Each span is its own seeked input joined
                by the concat filter (the select/aselect approach can stall some ffmpeg builds).
    music       background track, looped, quiet, and ducked whenever the clip itself is loud
                (sidechain compression), so speech stays clear.
    on_progress callback(pct 0-100) while ffmpeg renders.
    zoom_times  punch-in zooms, in output time (after jump cuts)
    intro_image the cover shown as the first frames, so every platform's
                auto-thumbnail and the first thing a viewer sees is the cover. It is
                encoded separately and joined without re-encoding (second, cheap pass).
    max_mb (optional) caps the file size; otherwise quality is capped at max_kbps
    (Telegram gets a separate preview when a clip is too big, see make_preview).
    """
    info = probe(src)
    duration = max(0.5, min(end, info.duration) - start)
    target = out_w / out_h
    if crop_x is None:
        crop_x = find_crop_x(src, start, duration, info, target_aspect=target) if info.aspect > target * 1.01 else 0
    spans = []
    for sp in keep or []:
        a, b = max(0.0, sp[0]), min(duration, sp[1])
        speed = float(sp[2]) if len(sp) > 2 and sp[2] else 1.0
        if b - a > 0.05:
            spans.append((a, b, max(0.5, min(2.0, speed))))
    if not spans:
        spans = [(0.0, duration, 1.0)]
    out_dur = sum((b - a) / sp for a, b, sp in spans)
    vf = frame_filter(info, crop_x, out_w, out_h)
    if zoom_times:
        vf += "," + zoom_filter(zoom_times, out_w, out_h)
    cwd = None
    if ass_file is not None and not can_burn_captions():
        log.warning("ffmpeg has no 'ass' filter (built without libass); rendering %s without captions", dst.name)
        ass_file = None
    if ass_file is not None:
        # Run ffmpeg *in* the subtitle's folder and pass a bare file name: this
        # sidesteps the filtergraph escaping of ':' '\\' and spaces in paths,
        # a classic cause of "Unable to open subtitles" failures.
        cwd = ass_file.parent
        vf += f",ass={ass_file.name}"

    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    for a, b, _sp in spans:
        cmd += ["-ss", f"{start + a:.3f}", "-t", f"{b - a:.3f}", "-i", src.resolve()]
    n = len(spans)
    use_music = music is not None and Path(music).exists()
    if use_music:
        cmd += ["-stream_loop", "-1", "-i", Path(music).resolve()]
    parts = []
    if n == 1 and spans[0][2] == 1.0:
        v_in, a_in = "[0:v]", "[0:a:0]"
        graph = f"[0:v]fps=30,{vf}[v0]"
    else:
        for i, (_a, _b, sp) in enumerate(spans):
            vpts = "PTS-STARTPTS" if sp == 1.0 else f"(PTS-STARTPTS)/{sp:.3f}"
            parts.append(f"[{i}:v]setpts={vpts},fps=30[cv{i}]")
            if info.has_audio:
                tempo = "" if sp == 1.0 else f",atempo={sp:.3f}"
                parts.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS{tempo}[ca{i}]")
        joined = "".join(f"[cv{i}]" + (f"[ca{i}]" if info.has_audio else "") for i in range(n))
        parts.append(f"{joined}concat=n={n}:v=1:a={1 if info.has_audio else 0}" + ("[vj][aj]" if info.has_audio else "[vj]"))
        a_in = "[aj]"
        graph = ";".join(parts) + f";[vj]{vf}[v0]"
    out_label = "[v0]"
    if progress_bar:
        bar_h = max(12, min(out_w, out_h) // 54)  # ~20 px on 1080 wide: visible on phones
        graph += (f";color=c=0x{bar_color}:s={out_w}x{bar_h}:r=30:d={out_dur:.3f}[bar];"
                  f"[v0][bar]overlay=x='-w+w*t/{out_dur:.3f}':y=H-h:eof_action=pass:eval=frame[v1]")
        out_label = "[v1]"
    graph += f";{out_label}format=yuv420p,setsar=1[vout]"
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11"
    if use_music:
        mu = (f"[{n}:a:0]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{out_dur:.3f},"
              f"volume={music_volume:.3f},afade=t=in:d=0.4,afade=t=out:st={max(0.0, out_dur - 0.4):.3f}:d=0.4")
        if info.has_audio:
            graph += (f";{a_in}aresample=48000,aformat=channel_layouts=stereo,asplit=2[va][vs]"
                      f";{mu}[mu];[mu][vs]sidechaincompress=threshold=0.02:ratio=12:attack=10:release=400[duck]"
                      f";[va][duck]amix=inputs=2:duration=first:normalize=0,{norm}[aout]")
        else:
            graph += f";{mu},{norm}[aout]"
    elif info.has_audio:
        graph += f";{a_in}aresample=48000,aformat=channel_layouts=stereo,{norm}[aout]"
    has_audio_out = info.has_audio or use_music
    intro = intro_image is not None and intro_image.exists()
    total = out_dur + (intro_s if intro else 0.0)
    audio_kbps = 128
    vmax = max_kbps
    if max_mb:
        vmax = max(1500, min(max_kbps, int(max_mb * 8 * 1024 / total) - audio_kbps))
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
           "-maxrate", f"{vmax}k", "-bufsize", f"{vmax * 2}k", "-r", "30", "-video_track_timescale", "15360"]
    aenc = ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-ac", "2"]
    body = dst.with_name(dst.stem + "_body.mp4") if intro else dst
    cmd += ["-filter_complex", graph, "-map", "[vout]"]
    if has_audio_out:
        cmd += ["-map", "[aout]"] + aenc
    cmd += enc + ["-t", f"{out_dur:.3f}", "-movflags", "+faststart", body.resolve()]
    run(cmd, cwd=cwd, timeout=max(300.0, out_dur * 30),
        on_progress=(lambda pct: on_progress(pct * 0.97)) if on_progress else None, total_s=out_dur)
    if intro:
        _prepend_intro(intro_image, body, dst, out_w, out_h, intro_s, has_audio_out, enc, aenc)
        body.unlink(missing_ok=True)
    size_mb = dst.stat().st_size / 1024 / 1024
    log.info("rendered %s (%.1fs, %.1f MB, crop_x=%s, spans=%d, zooms=%d, intro=%s, music=%s)", dst.name, total,
             size_mb, crop_x, n, len(zoom_times or []), intro, use_music)
    if on_progress:
        on_progress(100.0)
    return dst


def _prepend_intro(image: Path, body: Path, dst: Path, out_w: int, out_h: int, intro_s: float,
                   has_audio: bool, enc: list, aenc: list) -> None:
    """Encode the cover as a short clip with the body's exact settings, then join both without re-encoding."""
    intro = dst.with_name(dst.stem + "_intro.mp4")
    lst = dst.with_name(dst.stem + "_concat.txt")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-loop", "1", "-framerate", "30", "-t", f"{intro_s:.3f}",
           "-i", image.resolve()]
    if has_audio:
        cmd += ["-f", "lavfi", "-t", f"{intro_s:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1,"
                   f"format=yuv420p", "-map", "0:v"]
    if has_audio:
        cmd += ["-map", "1:a"] + aenc
    cmd += enc + ["-t", f"{intro_s:.3f}", intro.resolve()]
    try:
        run(cmd, timeout=120)
        lst.write_text(f"file '{intro.resolve()}'\nfile '{body.resolve()}'\n")
        run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst.resolve(),
             "-c", "copy", "-movflags", "+faststart", dst.resolve()], timeout=300)
    finally:
        intro.unlink(missing_ok=True)
        lst.unlink(missing_ok=True)


def activity(src: Path, start: float, duration: float, step: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Per-step on-screen motion and loudness over a clip (used to tell dead air from quiet action)."""
    import subprocess

    import cv2

    n = max(1, int(duration / step))
    motion = np.zeros(n)
    cap = cv2.VideoCapture(str(src))
    prev = None
    for i in range(n + 1):
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + i * step) * 1000)
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (160, max(1, int(160 * f.shape[0] / f.shape[1])))),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None and i - 1 < n:
            motion[i - 1] = float(np.abs(g - prev).mean())
        prev = g
    cap.release()
    sr = 8000
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                        "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True, timeout=120)
    pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(sr * step)
    loud = np.array([float(np.sqrt(np.mean(pcm[i * hop:(i + 1) * hop] ** 2))) if (i + 1) * hop <= len(pcm) else 0.0
                     for i in range(n)])
    return motion, loud


def dead_gap_checker(src: Path, start: float, duration: float, step: float = 0.25):
    """A gap is dead air only if it is BOTH visually calm and quiet compared with the rest of the clip.
    Silent fight scenes, gameplay and music stay in."""
    try:
        motion, loud = activity(src, start, duration, step)
    except Exception as e:  # analysis is best-effort; without it, cut nothing
        log.warning("activity analysis failed: %s", e)
        return lambda a, b: False
    m_ref = float(np.median(motion)) if motion.any() else 0.0
    l_ref = float(np.percentile(loud, 75)) if loud.any() else 0.0

    def is_dead(a: float, b: float) -> bool:
        i, j = int(a / step), max(int(a / step) + 1, int(b / step))
        m, lo = motion[i:j], loud[i:j]
        if not len(m):
            return False
        calm = float(m.mean()) <= 0.6 * m_ref + 0.5
        quiet = float(lo.mean()) <= 0.35 * l_ref
        return calm and quiet
    return is_dead


def jump_cut_spans(words, duration: float, min_gap: float = 0.7, pad: float = 0.15,
                   min_words: int = 6, gap_is_dead=None,
                   max_removed: float = 0.25) -> list[tuple[float, float]] | None:
    """Spans to keep when removing dead air.

    Candidate gaps are pauses between words longer than min_gap. A gap is only cut if
    gap_is_dead(a, b) agrees (calm and quiet: not a silent action scene), and at most
    max_removed of the clip is cut (longest dead gaps first). Returns None when there is
    nothing to cut or not enough speech to cut on."""
    if len(words) < min_words:
        return None
    spans: list[list[float]] = []
    for w in words:
        a, b = max(0.0, w.start - pad), min(duration, w.end + pad)
        if spans and a - spans[-1][1] < min_gap:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    gaps = []
    if spans[0][0] >= min_gap:
        gaps.append((0.0, spans[0][0]))
    gaps += [(spans[k][1], spans[k + 1][0]) for k in range(len(spans) - 1)]
    if duration - spans[-1][1] >= min_gap:
        gaps.append((spans[-1][1], duration))
    if gap_is_dead is not None:
        gaps = [g for g in gaps if gap_is_dead(*g)]
    budget, chosen = max_removed * duration, []
    for g in sorted(gaps, key=lambda g: g[0] - g[1]):  # longest first
        if g[1] - g[0] <= budget:
            chosen.append(g)
            budget -= g[1] - g[0]
    if not chosen:
        return None
    keep, t = [], 0.0
    for a, b in sorted(chosen):
        if a > t:
            keep.append((t, a))
        t = b
    if t < duration:
        keep.append((t, duration))
    return keep


def remap_time(t: float, keep: list | None) -> float | None:
    """Clip time -> output time after jump cuts (None if t falls in a removed gap).
    Monotonic spans only; see remap_all for teases/replays where a moment plays twice."""
    if not keep:
        return t
    acc = 0.0
    for sp in keep:
        a, b = sp[0], sp[1]
        speed = sp[2] if len(sp) > 2 and sp[2] else 1.0
        if t < a:
            return None
        if t <= b:
            return acc + (t - a) / speed
        acc += (b - a) / speed
    return None


def remap_all(t: float, spans: list) -> list[float]:
    """Every output time at which clip time t is shown (a payoff can appear in the tease, the body and the replay)."""
    out, acc = [], 0.0
    for sp in spans:
        a, b = sp[0], sp[1]
        speed = sp[2] if len(sp) > 2 and sp[2] else 1.0
        if a <= t <= b:
            out.append(acc + (t - a) / speed)
        acc += (b - a) / speed
    return out


def spans_duration(spans: list) -> float:
    return sum((sp[1] - sp[0]) / (sp[2] if len(sp) > 2 and sp[2] else 1.0) for sp in spans)


def payoff_time(src: Path, start: float, duration: float, step: float = 0.25) -> tuple[float, float]:
    """The clip's peak moment (seconds into the clip) and how strong it is (z-score).
    Loudness counts double, sudden change in motion counts once; the first 2.5 s are ignored
    (that's already the opening) so a tease of it is worth showing."""
    motion, loud = activity(src, start, duration, step)
    if not len(loud):
        return duration * 0.6, 0.0

    def z(x: np.ndarray) -> np.ndarray:
        return (x - x.mean()) / (x.std() or 1.0)
    k = max(1, int(1.0 / step))
    score = np.convolve(2 * z(loud) + z(motion), np.ones(k) / k, mode="same")
    lo = min(len(score) - 1, int(2.5 / step))
    hi = max(lo + 1, len(score) - int(0.5 / step))
    i = lo + int(np.argmax(score[lo:hi]))
    return min(duration, (i + 0.5) * step), float(score[i])


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


def cover_from_image(img: Path, dst: Path, out_w: int, out_h: int, ass_file: Path | None = None) -> Path:
    """A cover from the user's own picture: filled to the clip's shape, hook text on top."""
    vf = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1"
    cwd = None
    if ass_file is not None and can_burn_captions():
        cwd = ass_file.parent
        vf += f",ass={ass_file.name}"
    run(["ffmpeg", "-y", "-v", "error", "-i", img.resolve(), "-vf", vf, "-frames:v", "1", "-q:v", "3",
         dst.resolve()], cwd=cwd)
    return dst


render_vertical = render_clip  # backwards-compatible name
