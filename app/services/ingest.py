"""Source ingestion: phone uploads, existing video links, live streams.

GAP in the spec: it only handled a file POSTed from a phone. Clips from
existing video links and from live streams are added here.

  * download_url(): any site yt-dlp supports (YouTube, Twitch VODs/clips, Kick,
    X, Reddit, direct .mp4/.m3u8 ...). Capped at 1080p, merged to MP4.
  * record_live(): records a live stream into fixed-length MP4 segments with
    ffmpeg's segment muxer, so highlight detection can run on each finished
    segment while the stream is still going.

Only download content you own or have the rights to repost.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+", re.I)
DIRECT_MEDIA = re.compile(r"\.(mp4|mov|mkv|webm|m3u8|ts|flv)(\?|$)", re.I)


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,>") if m else None


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80]
    return stem or "video"


NETWORK_HINTS = ("ssl", "eof occurred", "timed out", "connection reset", "connection refused",
                 "temporary failure in name resolution", "network is unreachable", "remote end closed")


def is_network_error(msg: str) -> bool:
    m = msg.lower()
    return any(h in m for h in NETWORK_HINTS)


def _ydl_opts(dest_dir: Path, max_height: int, force_ipv4: bool) -> dict:
    opts = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
    }
    if force_ipv4:
        # Broken IPv6 routes and some VPNs/ISPs cut TLS to Google mid-handshake ("SSL: UNEXPECTED_EOF").
        opts["source_address"] = "0.0.0.0"
    return opts


def download_url(url: str, dest_dir: Path, max_height: int = 1080) -> tuple[Path, dict]:
    """Download a video link. Returns (path, metadata).

    Network failures (TLS dropped, timeouts) are retried once over IPv4, then
    reported as a plain-language error instead of yt-dlp's raw traceback text.
    """
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for force_ipv4 in (False, True):
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(dest_dir, max_height, force_ipv4)) as ydl:
                # Check first: downloading a live URL would never finish.
                info = ydl.extract_info(url, download=False)
                if info.get("is_live"):
                    raise ValueError("URL is a live stream; submit it to /ingest/live instead")
                info = ydl.process_ie_result(info, download=True)
                path = Path(ydl.prepare_filename(info))
            break
        except yt_dlp.utils.DownloadError as e:
            last = e
            if not is_network_error(str(e)):
                raise
            log.warning("network error downloading %s (ipv4=%s): %s", url, force_ipv4, e)
    else:
        raise ConnectionError(
            "Couldn't download the video: the connection to the site was cut off "
            "(often a VPN, proxy, firewall app or unstable network). Check your connection and retry. "
            f"Details: {str(last)[:200]}") from last
    if not path.exists():  # merged output may have a different extension
        candidates = sorted(dest_dir.glob(f"{info['id']}.*"), key=lambda p: p.stat().st_mtime)
        candidates = [c for c in candidates if c.suffix not in (".part", ".ytdl")]
        if not candidates:
            raise FileNotFoundError(f"yt-dlp finished but no file for {url}")
        path = candidates[-1]
    meta = {k: info.get(k) for k in ("id", "title", "uploader", "duration", "webpage_url", "extractor")}
    log.info("downloaded %s -> %s", url, path)
    return path, meta


def resolve_stream(url: str) -> str:
    """Turn a channel/watch URL into a direct media URL ffmpeg can read."""
    if DIRECT_MEDIA.search(url) or url.startswith(("rtmp://", "srt://", "file:")) or Path(url).exists():
        return url
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "format": "best[height<=1080]/best", "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info["url"]


def record_live(url: str, out_dir: Path, segment_s: int = 120, max_s: float = 6 * 3600,
                realtime_input: bool = False) -> subprocess.Popen:
    """Start ffmpeg recording the stream into out_dir/seg_00000.mp4, seg_00001.mp4 ...

    Stream copy (no re-encode) keeps CPU near zero while recording; each
    segment starts on a keyframe so it can be cut later.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    src = resolve_stream(url)
    cmd = ["ffmpeg", "-y", "-v", "error", "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "10"] if src.startswith("http") else ["ffmpeg", "-y", "-v", "error"]
    if realtime_input:
        cmd += ["-re"]
    cmd += ["-i", src, "-t", str(int(max_s)), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1",
            "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
            str(out_dir / "seg_%05d.mp4")]
    log.info("recording live %s -> %s", url, out_dir)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def finished_segments(out_dir: Path, proc: subprocess.Popen, poll_s: float = 5.0,
                      should_stop: Callable[[], bool] = lambda: False) -> Iterator[Path]:
    """Yield each segment once ffmpeg has moved on to the next one (or exited)."""
    seen: set[str] = set()
    while True:
        running = proc.poll() is None
        segs = sorted(out_dir.glob("seg_*.mp4"))
        ready = segs if not running else segs[:-1]  # newest one is still being written
        for s in ready:
            if s.name not in seen and s.stat().st_size > 0:
                seen.add(s.name)
                yield s
        if not running:
            if proc.returncode not in (0, None):
                err = proc.stderr.read() if proc.stderr else ""
                log.warning("live recorder exited %s: %s", proc.returncode, err[-500:])
            return
        if should_stop():
            proc.terminate()
        time.sleep(poll_s)


def have_tool(name: str) -> bool:
    return shutil.which(name) is not None
