"""Telegram message sending and callback handling.

FIXES vs the spec:
  * Telegram webhooks only accept HTTPS on ports 443/80/88/8443 with a valid
    certificate, so `http://STATIC_IP:8000/telegram-webhook` can never receive
    the Approve tap. Long polling (getUpdates) is the default: it needs no
    public URL, domain or certificate. The webhook stays available for setups
    that do have HTTPS (e.g. a free Cloudflare/Tailscale Funnel tunnel), and
    it checks Telegram's secret_token header.
  * Bots can upload at most 50 MB; renders are bitrate-capped below that and
    the size is checked before sending.
  * callback_data is limited to 64 bytes; it carries "a:<12-char job id>".
  * Anyone can message a bot. Only TELEGRAM_OWNER_CHAT_ID may approve or
    submit links; everyone else is ignored.
  * The bot doubles as an input: send it a video link from your phone's share
    sheet and it is queued for clipping.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
TG_MAX_UPLOAD = 50 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


def _api(method: str, *, data: dict | None = None, files: dict | None = None, timeout: float = 60,
         client: httpx.Client | None = None) -> Any:
    token = get_settings().telegram_bot_token
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=timeout)
    finally:
        if client is None:
            c.close()
    body = r.json()
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description')}")
    return body["result"]


def send_text(text: str, chat_id: int | None = None) -> None:
    _api("sendMessage", data={"chat_id": chat_id or get_settings().telegram_owner_chat_id, "text": text,
                              "disable_web_page_preview": "true"})


def send_for_approval(job_id: str, video: Path, caption: str, cover: Path | None = None) -> int:
    size = video.stat().st_size
    if size > TG_MAX_UPLOAD:
        raise TelegramError(f"{video.name} is {size/1e6:.1f} MB; Telegram bots can upload 50 MB max")
    markup = {"inline_keyboard": [[
        {"text": "✅ Approve & Publish", "callback_data": f"a:{job_id}"},
        {"text": "❌ Reject & Delete", "callback_data": f"r:{job_id}"},
    ]]}
    files = {}
    with video.open("rb") as fh:
        files["video"] = (video.name, fh, "video/mp4")
        cover_fh = None
        if cover is not None and cover.exists() and cover.stat().st_size < 200_000:
            cover_fh = cover.open("rb")  # Telegram thumbnails must be small JPEGs
            files["thumbnail"] = (cover.name, cover_fh, "image/jpeg")
        try:
            msg = _api("sendVideo", timeout=300, data={
                "chat_id": get_settings().telegram_owner_chat_id,
                "caption": caption[:1024],
                "supports_streaming": "true",
                "reply_markup": json.dumps(markup),
            }, files=files)
        finally:
            if cover_fh:
                cover_fh.close()
    if cover is not None and cover.exists():
        try:
            with cover.open("rb") as ch:
                _api("sendPhoto", timeout=60, data={"chat_id": get_settings().telegram_owner_chat_id,
                                                    "caption": "Cover image for the clip above",
                                                    "reply_to_message_id": msg["message_id"]},
                     files={"photo": (cover.name, ch, "image/jpeg")})
        except TelegramError as e:
            log.warning("cover send failed: %s", e)
    return int(msg["message_id"])


def handle_update(update: dict, on_approve: Callable[[str], str], on_reject: Callable[[str], str],
                  on_link: Callable[[str], str]) -> str | None:
    """Shared by the webhook endpoint and the long-poller. Returns a short status for logging."""
    owner = get_settings().telegram_owner_chat_id
    cq = update.get("callback_query")
    if cq:
        if int(cq.get("from", {}).get("id", 0)) != owner:
            return "ignored: not owner"
        action, _, job_id = (cq.get("data") or "").partition(":")
        result = on_approve(job_id) if action == "a" else on_reject(job_id) if action == "r" else "unknown"
        try:
            _api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": result[:190]})
            msg = cq.get("message") or {}
            if msg:
                _api("editMessageReplyMarkup", data={"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                                     "reply_markup": json.dumps({"inline_keyboard": []})})
        except TelegramError as e:  # stale callbacks can't be answered; not fatal
            log.warning("telegram ack failed: %s", e)
        return result
    msg = update.get("message") or {}
    if msg and int(msg.get("from", {}).get("id", 0)) == owner:
        from app.services.ingest import extract_url

        text = msg.get("text") or msg.get("caption") or ""
        if text.strip().lower() in ("/start", "/help"):
            send_text("Send me a video or live-stream link. Add format words to choose the shape, e.g.\n"
                      "https://youtu.be/... landscape square\n"
                      "Formats: vertical (9:16), portrait (4:5), square (1:1), landscape (16:9).",
                      chat_id=msg["chat"]["id"])
            return "help"
        url = extract_url(text)
        if url:
            result = on_link(url, text)
            send_text(result, chat_id=msg["chat"]["id"])
            return result
    return None


def poll_forever(on_approve, on_reject, on_link, stop: Callable[[], bool] = lambda: False) -> None:
    """Long-poll getUpdates. Use this instead of a webhook when the server has no HTTPS domain."""
    try:
        _api("deleteWebhook", data={"drop_pending_updates": "false"})
    except TelegramError as e:
        log.warning("deleteWebhook: %s", e)
    offset = 0
    with httpx.Client(timeout=70) as client:
        while not stop():
            try:
                updates = _api("getUpdates", client=client, timeout=70, data={
                    "offset": offset, "timeout": 50,
                    "allowed_updates": json.dumps(["callback_query", "message"])})
            except (httpx.HTTPError, TelegramError) as e:
                log.warning("getUpdates failed: %s; retrying", e)
                time.sleep(5)
                continue
            for u in updates:
                offset = max(offset, int(u["update_id"]) + 1)
                try:
                    log.info("update %s -> %s", u["update_id"], handle_update(u, on_approve, on_reject, on_link))
                except Exception:  # never let one bad update kill the poller
                    log.exception("update handling failed")
