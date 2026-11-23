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
    long = round(max_height * 16 / 9)
    opts = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        # cap the long side, so vertical 1080x1920 videos still come in 1080p
        "format": f"bv*[height<={long}][width<={long}]+ba/b[height<={long}][width<={long}]/bv*+ba/b",
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


def _progress_hook(on_progress):
    state = {"part": 0, "last_file": None}

    def hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        fn = d.get("filename")
        if fn != state["last_file"]:
            state["last_file"], state["part"] = fn, state["part"] + 1
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        pct = done / total * 100 if total else None
        speed = d.get("speed") or 0
        eta = d.get("eta")
        bits = [f"{done / 1e6:.0f}/{total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"]
        if speed:
            bits.append(f"{speed / 1e6:.1f} MB/s")
        if eta:
            bits.append(f"{int(eta) // 60}:{int(eta) % 60:02d} left")
        what = "video" if state["part"] == 1 else "audio" if state["part"] == 2 else f"part {state['part']}"
        try:
            on_progress("download", detail=f"Downloading {what}: " + " · ".join(bits), pct=pct)
        except Exception:
            pass
    return hook


def download_url(url: str, dest_dir: Path, max_height: int = 1080, on_progress=None) -> tuple[Path, dict]:
    """Download a video link. Returns (path, metadata).

    Network failures (TLS dropped, timeouts) are retried once over IPv4, then
    reported as a plain-language error instead of yt-dlp's raw traceback text.
    """
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for force_ipv4 in (False, True):
        try:
            opts = _ydl_opts(dest_dir, max_height, force_ipv4)
            if on_progress is not None:
                opts["progress_hooks"] = [_progress_hook(on_progress)]
            with yt_dlp.YoutubeDL(opts) as ydl:
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
    from app.services.rights import RIGHTS_FIELDS

    meta["rights_info"] = {k: info.get(k) for k in RIGHTS_FIELDS}
    log.info("downloaded %s -> %s", url, path)
    return path, meta


def probe_url(url: str) -> dict:
    """Metadata only (no download), used by the copyright check."""
    import yt_dlp

    last: Exception | None = None
    for force_ipv4 in (False, True):
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "socket_timeout": 20}
        if force_ipv4:
            opts["source_address"] = "0.0.0.0"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False, process=False) or {}
        except yt_dlp.utils.DownloadError as e:
            last = e
            if not is_network_error(str(e)):
                raise
    raise ConnectionError(f"Couldn't reach the site to check this link: {str(last)[:200]}") from last


# Live streams: YouTube sometimes offers combined HLS formats (video + audio in one .m3u8), but often only
# separate video and audio streams. Try combined first, then a separate pair (ffmpeg reads both), then anything.
# Vertical streams are 1080x1920, so cap the LONG side at 1920 (a height<=1080 cap would drop them to 480p).
LIVE_FORMATS = ("b[vcodec!=none][acodec!=none][height<=1920][width<=1920]"
                "/bv*[height<=1920][width<=1920]+ba/bv*+ba/b")


def live_error_message(err: str, info: dict | None = None) -> str:
    status = (info or {}).get("live_status")
    if status == "is_upcoming" or "will begin" in err or "Premieres in" in err:
        return "This live stream hasn't started yet. Try again when it is live."
    if status in ("was_live", "not_live", "post_live"):
        return "This stream isn't live any more. Paste it under Link (not Live) to clip the recording."
    if "available formats" in err or "Requested format" in err:
        return ("YouTube didn't offer a recordable format for this stream (often fixed by updating yt-dlp: "
                "pip install -U yt-dlp). Details: " + err[:200])
    return err[:300]


def resolve_streams(url: str) -> tuple[list[str], dict]:
    """Turn a channel/watch URL into the direct media URL(s) ffmpeg can read: one combined stream, or a video
    and an audio stream. Returns (urls, http_headers)."""
    if DIRECT_MEDIA.search(url) or url.startswith(("rtmp://", "srt://", "file:")) or Path(url).exists():
        return [url], {}
    import yt_dlp

    base = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}
    info: dict = {}
    last = ""
    for fmt in (LIVE_FORMATS, None):  # None = let yt-dlp pick its default
        opts = dict(base, **({"format": fmt} if fmt else {}))
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except yt_dlp.utils.DownloadError as e:
            last = str(e)
            if "format" not in last.lower():
                break
            continue
        fmts = info.get("requested_formats") or ([info] if info.get("url") else [])
        urls = [f["url"] for f in fmts if f.get("url")]
        if urls:
            headers = dict((fmts[0].get("http_headers") or info.get("http_headers") or {}))
            log.info("live %s: format %s (%s)", url, info.get("format_id"), info.get("live_status"))
            return urls[:2], headers
        last = "no stream URL in the site's answer"
    raise ValueError(live_error_message(last, info))


def resolve_stream(url: str) -> str:
    """Back-compat: the first media URL."""
    return resolve_streams(url)[0][0]


def record_live(url: str, out_dir: Path, segment_s: int = 120, max_s: float = 6 * 3600,
                realtime_input: bool = False) -> subprocess.Popen:
    """Start ffmpeg recording the stream into out_dir/seg_00000.mp4, seg_00001.mp4 ...

    Stream copy (no re-encode) keeps CPU near zero while recording; each
    segment starts on a keyframe so it can be cut later.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    urls, headers = resolve_streams(url)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for src in urls:
        if src.startswith("http"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
            if headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        if realtime_input:
            cmd += ["-re"]
        cmd += ["-i", src]
    audio = "1:a:0" if len(urls) > 1 else "0:a:0?"
    cmd += ["-t", str(int(max_s)), "-map", "0:v:0", "-map", audio, "-c", "copy",
            "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1",
            "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
            str(out_dir / "seg_%05d.mp4")]
    log.info("recording live %s -> %s (%d input%s)", url, out_dir, len(urls), "s" if len(urls) > 1 else "")
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
            if proc.returncode not in (0, None) and not should_stop():
                err = proc.stderr.read() if proc.stderr else ""
                log.warning("live recorder exited %s: %s", proc.returncode, err[-500:])
                if not seen:  # it never recorded anything: say why instead of finishing quietly
                    raise RuntimeError(f"recording stopped before the first segment: {err.strip()[-300:] or proc.returncode}")
            return
        if should_stop():
            proc.terminate()
        time.sleep(poll_s)


def have_tool(name: str) -> bool:
    return shutil.which(name) is not None
