"""Find viral moments without keeping the whole video or stream.

Finished videos and ended streams (YouTube):
    YouTube's "Most replayed" graph (yt-dlp exposes it as `heatmap`: ~100 buckets of how often each part is
    rewatched) shows where the moments are. heatmap_peaks() picks the highest peaks and download_sections()
    downloads only those parts (a few minutes instead of a 3-hour VOD).

Streams that are still live:
    There is no graph yet, so ClipForge keeps a short rolling buffer (RollingBuffer: ffmpeg overwrites the same
    few segment files, so only the last few minutes are ever on disk) and watches the live chat
    (YouTubeChat via yt-dlp, TwitchChat via anonymous IRC). SpikeDetector flags moments when chat suddenly
    speeds up or fills with reactions ("LMAO", "KEKW", "💀", "clip it"). The buffer window around each spike is
    saved as a clip; everything else is overwritten.

When a stream has no readable chat (or chat is quiet), the audio/motion level of each buffered segment is the
fallback signal, still without keeping the whole stream.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import socket
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- "Most replayed" graph
def heatmap_peaks(heatmap: list[dict] | None, duration: float, clip_s: float, n: int,
                  min_gap_s: float | None = None, min_lift: float = 1.25) -> list[tuple[float, float, float]]:
    """Top n moments from YouTube's Most-replayed graph: [(start, end, value)], sorted by time.

    Each window is clip_s long and starts a little before the peak (people rewatch the payoff, so the setup
    comes first). Peaks must stand out (value >= min_lift x the median) and be min_gap_s apart."""
    pts = [(float(h.get("start_time") or 0), float(h.get("end_time") or 0), float(h.get("value") or 0))
           for h in (heatmap or []) if h.get("end_time") is not None]
    if len(pts) < 5 or not duration:
        return []
    med = statistics.median(v for _, _, v in pts) or 1e-6
    gap = min_gap_s if min_gap_s is not None else max(clip_s * 1.5, 60.0)
    chosen: list[tuple[float, float, float]] = []
    for a, b, v in sorted(pts, key=lambda p: -p[2]):
        if v < min_lift * med:
            break
        peak = (a + b) / 2
        if any(abs(peak - (c[0] + c[1]) / 2) < gap for c in chosen):
            continue
        start = max(0.0, min(duration - clip_s, peak - clip_s * 0.65))
        chosen.append((round(start, 1), round(min(duration, start + clip_s), 1), round(v, 3)))
        if len(chosen) >= n:
            break
    return sorted(chosen)


def download_sections(url: str, ranges: list[tuple[float, float]], dest_dir: Path, max_height: int = 1080,
                      on_progress=None) -> list[Path]:
    """Download only these time ranges of a video (one MP4 per range, same order)."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    from app.services.ingest import _progress_hook, _ydl_opts

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = _ydl_opts(dest_dir, max_height, False)
    opts.update({"outtmpl": str(dest_dir / "%(id)s_%(section_start)06d.%(ext)s"),
                 "download_ranges": download_range_func(None, [(a, b) for a, b in ranges]),
                 "force_keyframes_at_cuts": False})
    if on_progress is not None:
        opts["progress_hooks"] = [_progress_hook(on_progress)]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vid = info.get("id") or ""
    out = []
    for a, _b in ranges:
        found = sorted(p for p in dest_dir.glob(f"{vid}_{int(a):06d}.*") if p.suffix not in (".part", ".ytdl"))
        if found:
            out.append(found[-1])
    if not out:
        raise FileNotFoundError("yt-dlp finished but no section files were written")
    return out


# ---------------------------------------------------------------- live chat
HYPE = re.compile(r"(\blo+l\b|\blmf?a+o+\b|\bkekw\b|\bomegalul\b|\blul\b|\bpog\w*\b|\bw+\b|\bno+ wa+y\b|"
                  r"\bclip\b|\bclip it\b|\bholy\b|\bwtf\b|\bomg\b|\bdead\b|\?{3,}|!{3,}|"
                  r"😂|🤣|💀|😭|🔥|😱|🤯|:face-with-tears-of-joy:|:skull:|:fire:)", re.I)


class ChatSource:
    """Collects (wall_time, text) from a live chat on a background thread."""

    name = "chat"

    def __init__(self) -> None:
        self.messages: collections.deque[tuple[float, str]] = collections.deque(maxlen=20000)
        self.error: str | None = None
        self.started = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "ChatSource":
        self._thread = threading.Thread(target=self._run_safe, daemon=True)
        self._thread.start()
        return self

    def _run_safe(self) -> None:
        try:
            self.run()
        except Exception as e:  # chat is a signal, never a reason to stop recording
            self.error = str(e)[:300]
            log.warning("%s stopped: %s", self.name, e)

    def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def add(self, text: str, at: float | None = None) -> None:
        self.messages.append((at or time.time(), text))


class TwitchChat(ChatSource):
    name = "Twitch chat"

    def __init__(self, channel: str, host: str = "irc.chat.twitch.tv", port: int = 6667):
        super().__init__()
        self.channel, self.host, self.port = channel.lower().lstrip("#"), host, port

    def run(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=30)
        s.settimeout(5)
        s.sendall(b"PASS SCHMOOPIIE\r\nNICK justinfan%d\r\nJOIN #%s\r\n" % (int(time.time()) % 90000 + 10000,
                                                                          self.channel.encode()))
        buf = b""
        while not self._stop.is_set():
            try:
                data = s.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            *lines, buf = buf.split(b"\r\n")
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                if line.startswith("PING"):
                    s.sendall(b"PONG" + raw[4:] + b"\r\n")
                elif " PRIVMSG " in line:
                    self.add(line.split(" :", 1)[-1] if " :" in line else "")
        s.close()


class YouTubeChat(ChatSource):
    """Runs yt-dlp's live-chat downloader (it keeps going until the stream ends) and reads its output."""

    name = "YouTube chat"

    def __init__(self, url: str, work_dir: Path):
        super().__init__()
        self.url, self.work_dir = url, work_dir
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        import sys

        self.work_dir.mkdir(parents=True, exist_ok=True)
        for f in self.work_dir.glob("chat*"):
            f.unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
             "--no-warnings", "--quiet", "-o", str(self.work_dir / "chat"), self.url],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        pos: dict[str, int] = {}
        while not self._stop.is_set():
            for f in self.work_dir.glob("chat*live_chat.json*"):
                with open(f, "rb") as fh:
                    fh.seek(pos.get(f.name, 0))
                    chunk = fh.read()
                    keep = chunk.rfind(b"\n") + 1
                    pos[f.name] = pos.get(f.name, 0) + keep
                for line in chunk[:keep].splitlines():
                    self._parse(line)
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() if self.proc.stderr else "") or ""
                if self.proc.returncode:
                    raise RuntimeError(f"chat reader exited: {err.strip()[-200:]}")
                break
            self._stop.wait(1.0)
        if self.proc.poll() is None:
            self.proc.terminate()

    def _parse(self, line: bytes) -> None:
        try:
            d = json.loads(line)
        except ValueError:
            return
        for act in (d.get("replayChatItemAction") or {}).get("actions") or []:
            item = ((act.get("addChatItemAction") or {}).get("item") or {})
            r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not r:
                continue
            parts = []
            for run in (r.get("message") or {}).get("runs") or []:
                if "text" in run:
                    parts.append(run["text"])
                elif "emoji" in run:
                    sc = (run["emoji"].get("shortcuts") or [""])[0]
                    parts.append(run["emoji"].get("emojiId") if len(run["emoji"].get("emojiId", "")) <= 2 else sc)
            try:
                at = int(r.get("timestampUsec")) / 1e6
            except (TypeError, ValueError):
                at = None
            self.add(" ".join(p for p in parts if p), at if at and abs(at - time.time()) < 600 else None)

    def stop(self) -> None:
        super().stop()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def chat_for(url: str, info: dict | None, work_dir: Path) -> ChatSource | None:
    """The right chat reader for this stream, or None (then audio/motion is the signal)."""
    u = url.lower()
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]{3,25})(?:[/?#]|$)", url)
    if m and m.group(1).lower() not in ("videos", "directory", "settings"):
        return TwitchChat(m.group(1))
    if "youtube.com" in u or "youtu.be" in u:
        return YouTubeChat((info or {}).get("webpage_url") or url, work_dir)
    return None


@dataclass
class Spike:
    at: float  # wall time of the spike (chat lags the moment by a few seconds)
    score: float  # how far above normal (x baseline)
    reason: str


class SpikeDetector:
    """Chat speed (messages per second over the last window_s) against the median of the last few minutes,
    plus the share of reaction messages ("LMAO", "💀", "clip it"...)."""

    def __init__(self, window_s: float = 10.0, history_s: float = 300.0, ratio: float = 2.5,
                 min_msgs: int = 8, hype_share: float = 0.35, cooldown_s: float = 45.0, warmup_s: float = 60.0):
        self.window_s, self.history_s, self.ratio = window_s, history_s, ratio
        self.min_msgs, self.hype_share, self.cooldown_s, self.warmup_s = min_msgs, hype_share, cooldown_s, warmup_s
        self.rates: collections.deque[tuple[float, float]] = collections.deque()
        self.last_spike = 0.0
        self.first: float | None = None
        self.last_rate = 0.0
        self.baseline = 0.0

    def check(self, messages, now: float | None = None) -> Spike | None:
        now = now or time.time()
        recent = [t for t, _ in messages if now - self.window_s <= t <= now]
        texts = [x for t, x in messages if now - self.window_s <= t <= now]
        if self.first is None and messages:
            self.first = min(t for t, _ in messages)
        rate = len(recent) / self.window_s
        hype = sum(1 for x in texts if HYPE.search(x or "")) / max(1, len(texts))
        while self.rates and now - self.rates[0][0] > self.history_s:
            self.rates.popleft()
        base = statistics.median(r for _, r in self.rates) if len(self.rates) >= 5 else None
        self.rates.append((now, rate))
        self.last_rate, self.baseline = rate, base or rate
        if self.first is None or now - self.first < self.warmup_s or now - self.last_spike < self.cooldown_s:
            return None
        if len(recent) < self.min_msgs:
            return None
        lift = rate / max(base or rate, 0.05)
        reason = None
        if base is not None and lift >= self.ratio:
            reason = f"chat {lift:.1f}x faster than normal"
        elif hype >= self.hype_share and lift >= 1.4:
            reason = f"{hype:.0%} of chat reacting ({len(recent)} msgs in {self.window_s:.0f} s)"
        if not reason:
            return None
        self.last_spike = now
        return Spike(now, round(lift + hype, 2), reason)


# ---------------------------------------------------------------- rolling buffer
class RollingBuffer:
    """ffmpeg writes the stream into seg_0..seg_{n-1} and starts over (segment_wrap), so only the last
    keep_s seconds exist on disk. A CSV list tells us when each segment finished (wall clock)."""

    def __init__(self, urls: list[str], headers: dict, out_dir: Path, segment_s: int = 10, keep_s: int = 300,
                 max_s: float = 6 * 3600, realtime_input: bool = False):
        self.out_dir, self.segment_s = out_dir, segment_s
        self.wrap = max(4, int(keep_s // segment_s))
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.glob("buf_*.mp4"):
            f.unlink(missing_ok=True)
        self.list_file = out_dir / "buffer.csv"
        self.list_file.write_text("")
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for src in urls:
            if str(src).startswith("http"):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
                if headers:
                    cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            if realtime_input:
                cmd += ["-re"]
            cmd += ["-i", str(src)]
        audio = "1:a:0" if len(urls) > 1 else "0:a:0?"
        cmd += ["-t", str(int(max_s)), "-map", "0:v:0", "-map", audio, "-c", "copy",
                "-f", "segment", "-segment_time", str(segment_s), "-segment_wrap", str(self.wrap),
                "-reset_timestamps", "1", "-segment_list", str(self.list_file), "-segment_list_type", "csv",
                "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
                str(out_dir / "buf_%03d.mp4")]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.segments: collections.deque[dict] = collections.deque(maxlen=self.wrap - 1)
        self._read = 0
        self.total_s = 0.0
        self.count = 0
        self.first_wall = time.time()

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def poll(self) -> list[dict]:
        """New finished segments since the last call: {file, dur, wall_start, wall_end, n}."""
        now = time.time()
        try:
            text = self.list_file.read_text()
        except OSError:
            return []
        lines = text.splitlines()
        if text and not text.endswith("\n"):
            lines = lines[:-1]  # still being written
        new = []
        for line in lines[self._read:]:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            try:
                dur = max(0.1, float(parts[2]) - float(parts[1]))
            except ValueError:
                continue
            seg = {"file": self.out_dir / parts[0], "dur": dur, "wall_end": now, "wall_start": now - dur,
                   "n": self.count}
            # the previous segment ended where this one starts (smoother than our polling clock)
            if self.segments:
                seg["wall_start"] = max(self.segments[-1]["wall_end"], now - dur)
                seg["wall_end"] = seg["wall_start"] + dur
            if self.count == 0:
                self.first_wall = seg["wall_start"]
            self.segments.append(seg)
            self.count += 1
            self.total_s += dur
            new.append(seg)
        self._read = len(lines)
        return new

    @property
    def latest_end(self) -> float:
        return self.segments[-1]["wall_end"] if self.segments else 0.0

    def save_window(self, a: float, b: float, dst: Path) -> tuple[Path, float, float] | None:
        """Copy the buffered segments covering wall times [a, b] into one MP4 (stream copy, no re-encode).
        Returns (file, start, end) with start/end inside that file, or None if it's no longer buffered."""
        segs = [s for s in self.segments if s["wall_end"] > a and s["wall_start"] < b and s["file"].exists()]
        if not segs:
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".parts")
        tmp.mkdir(exist_ok=True)
        try:
            copies = []
            for i, s in enumerate(segs):  # copy first: ffmpeg will overwrite these files soon
                c = tmp / f"{i:03d}.mp4"
                shutil.copy2(s["file"], c)
                copies.append(c)
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{c.resolve()}'\n" for c in copies))
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i",
                            str(lst), "-c", "copy", "-movflags", "+faststart", str(dst)], check=True,
                           capture_output=True, timeout=300)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        t0 = segs[0]["wall_start"]
        total = sum(s["dur"] for s in segs)
        return dst, round(max(0.0, a - t0), 2), round(min(total, b - t0), 2)

    def stop(self) -> None:
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def cleanup(self) -> None:
        for f in self.out_dir.glob("buf_*.mp4"):
            f.unlink(missing_ok=True)
        self.list_file.unlink(missing_ok=True)

    def error(self) -> str:
        if self.proc.poll() is None or not self.proc.stderr:
            return ""
        try:
            return self.proc.stderr.read()[-300:].strip()
        except Exception:
            return ""
