"""Command line entry points.

    python -m app.cli serve                              # web UI + Telegram bot + workers in one process (Mac/PC)
    python -m app.cli telegram-setup                     # find your chat id and save it to .env
    python -m app.cli clip <file-or-url> [--formats vertical,landscape] [--clips 3]
    python -m app.cli live <stream-url> [--minutes 30]   # record + auto-clip highlights
    python -m app.cli poll                               # Telegram long-poller only (Docker/server)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from app.utils.logs import quiet_http_logs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clip")
    c.add_argument("source")
    c.add_argument("--title")
    c.add_argument("--formats", help="comma list: vertical,portrait,square,landscape")
    c.add_argument("--clips", type=int)
    c.add_argument("--seconds", type=float, help="target clip length")
    lv = sub.add_parser("live")
    lv.add_argument("url")
    lv.add_argument("--minutes", type=float, default=30)
    lv.add_argument("--title")
    lv.add_argument("--realtime", action="store_true", help="read a local file at real-time speed (testing)")
    lv.add_argument("--formats")
    sub.add_parser("poll")
    sv = sub.add_parser("serve")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sub.add_parser("telegram-setup")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    quiet_http_logs()

    if a.cmd == "serve":
        return serve(a.host, a.port)
    if a.cmd == "telegram-setup":
        return telegram_setup()

    if a.cmd == "poll":
        from app import pipeline
        from app.services import telegram_bot

        pipeline.start_scheduler()
        telegram_bot.poll_forever(pipeline.approve, pipeline.reject, pipeline.submit_link,
                                  on_edit=pipeline.edit_clip, on_check=pipeline.check_text)
        return 0

    os.environ["CELERY_EAGER"] = "1"  # everything inline
    from app import pipeline
    from app.config import get_settings
    from app.utils.job_store import get_store

    get_settings.cache_clear()
    store = get_store()
    if a.cmd == "clip":
        opts = pipeline.clean_options(a.formats, a.clips, a.seconds)
        p = Path(a.source)
        if p.exists():
            job = pipeline.submit("upload", p.name, raw_path=p.resolve(), title=a.title or p.stem, options=opts)
        else:
            job = pipeline.submit("url", a.source, title=a.title, options=opts)
    else:
        os.environ["LIVE_MAX_HOURS"] = str(a.minutes / 60)
        get_settings.cache_clear()
        job = store.create("source", source_type="live", source=a.url, title=a.title,
                           meta={"options": pipeline.clean_options(a.formats)})
        pipeline.record_live(job, realtime_input=a.realtime)
    src = store.get(job)
    clips = store.children(job)
    print(json.dumps({"job": job, "status": src["status"], "error": src["error"],
                      "clips": [{k: c[k] for k in ("id", "status", "start_s", "end_s", "output_path", "error")}
                                for c in clips]}, indent=2))
    return 0 if src["status"] != "failed" and all(c["status"] != "failed" for c in clips) else 1


def serve(host: str | None, port: int | None) -> int:
    """Everything in one process: API + web UI, background workers, Telegram long-poller."""
    import threading
    import webbrowser

    import uvicorn

    os.environ.setdefault("TASK_MODE", "thread")
    os.environ.pop("CELERY_EAGER", None)
    from app.config import get_settings
    from app.utils.job_store import get_store

    get_settings.cache_clear()
    s = get_settings()
    n = get_store().mark_interrupted()
    if n:
        logging.info("%d job(s) were interrupted by the last shutdown; retry them from the UI", n)
    from app import pipeline

    pipeline.start_scheduler()
    if s.telegram_bot_token and s.telegram_owner_chat_id:
        from app.services import telegram_bot

        threading.Thread(target=telegram_bot.poll_forever, daemon=True, name="telegram",
                         args=(pipeline.approve, pipeline.reject, pipeline.submit_link),
                         kwargs={"on_edit": pipeline.edit_clip, "on_check": pipeline.check_text}).start()
        logging.info("Telegram bot is listening")
    else:
        logging.info("Telegram not configured (run `python -m app.cli telegram-setup`)")
    host, port = host or s.host, port or s.port
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  ClipForge UI: {url}\n")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
    return 0


def telegram_setup(env_file: Path = Path(".env"), wait_s: int = 300) -> int:
    """Check the bot token, wait for the owner to message the bot, save their chat id to .env."""
    import time

    from app.config import get_settings
    from app.services import telegram_bot

    get_settings.cache_clear()
    s = get_settings()
    if not s.telegram_bot_token:
        print("Add TELEGRAM_BOT_TOKEN=<token from @BotFather> to .env first.")
        return 1
    me = telegram_bot._api("getMe")
    telegram_bot._api("deleteWebhook", data={"drop_pending_updates": "false"})
    print(f"Bot OK: @{me['username']}. Open https://t.me/{me['username']} and send it /start ...")
    deadline, offset = time.time() + wait_s, 0
    while time.time() < deadline:
        ups = telegram_bot._api("getUpdates", timeout=40, data={"offset": offset, "timeout": 30})
        for u in ups:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if msg.get("from"):
                chat_id = msg["from"]["id"]
                set_env(env_file, "TELEGRAM_OWNER_CHAT_ID", str(chat_id))
                telegram_bot._api("getUpdates", data={"offset": offset, "timeout": 0})  # consume
                get_settings.cache_clear()
                telegram_bot.send_text("✅ Connected. Send me any video link and I'll make clips.", chat_id=chat_id)
                print(f"Saved TELEGRAM_OWNER_CHAT_ID={chat_id} to {env_file}")
                return 0
    print("No message received; run this again and send /start to the bot.")
    return 1


def set_env(env_file: Path, key: str, value: str) -> None:
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    env_file.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    sys.exit(main())
