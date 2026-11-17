"""Run the pipeline without Redis/Celery/Telegram:

    python -m app.cli clip <file-or-url> [--title T]     # render clips, print paths
    python -m app.cli live <stream-url> [--minutes 30]   # record + auto-clip highlights
    python -m app.cli poll                               # Telegram long-poller (production)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clip-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clip")
    c.add_argument("source")
    c.add_argument("--title")
    lv = sub.add_parser("live")
    lv.add_argument("url")
    lv.add_argument("--minutes", type=float, default=30)
    lv.add_argument("--title")
    lv.add_argument("--realtime", action="store_true", help="read a local file at real-time speed (testing)")
    sub.add_parser("poll")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "poll":
        from app import pipeline
        from app.services import telegram_bot

        telegram_bot.poll_forever(pipeline.approve, pipeline.reject, pipeline.submit_link)
        return 0

    os.environ["CELERY_EAGER"] = "1"  # everything inline
    from app import pipeline
    from app.config import get_settings
    from app.utils.job_store import get_store

    get_settings.cache_clear()
    store = get_store()
    if a.cmd == "clip":
        p = Path(a.source)
        if p.exists():
            job = pipeline.submit("upload", p.name, raw_path=p.resolve(), title=a.title or p.stem)
        else:
            job = pipeline.submit("url", a.source, title=a.title)
    else:
        os.environ["LIVE_MAX_HOURS"] = str(a.minutes / 60)
        get_settings.cache_clear()
        job = store.create("source", source_type="live", source=a.url, title=a.title)
        pipeline.record_live(job, realtime_input=a.realtime)
    src = store.get(job)
    clips = store.children(job)
    print(json.dumps({"job": job, "status": src["status"], "error": src["error"],
                      "clips": [{k: c[k] for k in ("id", "status", "start_s", "end_s", "output_path", "error")}
                                for c in clips]}, indent=2))
    return 0 if src["status"] != "failed" and all(c["status"] != "failed" for c in clips) else 1


if __name__ == "__main__":
    sys.exit(main())
