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


def approval_markup(job_id: str) -> dict:
    """Approve/Reject, edit buttons, and one tick-box per connected platform (all ticked by default).
    Tapping a platform flips it; only the ticked ones get the clip."""
    from app.pipeline import chosen_platforms
    from app.services.social_publisher import PLATFORMS, publishers_configured
    from app.utils.job_store import get_store

    configured = publishers_configured()
    chosen = chosen_platforms(((get_store().get(job_id) or {}).get("meta") or {}), configured)
    rows = [
        [{"text": "✅ Approve & Publish", "callback_data": f"a:{job_id}"},
         {"text": "❌ Reject & Delete", "callback_data": f"r:{job_id}"}],
        [{"text": "✏️ Title", "callback_data": f"et:{job_id}"},
         {"text": "🪝 Hook", "callback_data": f"eh:{job_id}"},
         {"text": "🖼 Cover", "callback_data": f"ec:{job_id}"}],
    ]
    # Every platform is shown; ones not connected yet are locked (🔒) and explain how to connect them.
    toggles = [{"text": f"{('☑️' if key in chosen else '⬜') if configured.get(key) else '🔒'} {label}",
                "callback_data": f"p:{job_id}:{code}"} for key, label, code in PLATFORMS]
    rows += [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    return {"inline_keyboard": rows}


def _toggle_platform(rest: str) -> str:
    from app.pipeline import toggle_platform
    from app.services.social_publisher import PLATFORMS, publishers_configured

    job_id, _, code = rest.partition(":")
    key = next((k for k, _, c in PLATFORMS if c == code), None)
    if not key:
        return "unknown platform"
    names = dict((k, label) for k, label, _ in PLATFORMS)
    if not publishers_configured().get(key):
        return f"🔒 {names[key]} isn't connected yet. Connect it in the app: Publishing."
    return "Will post to: " + ", ".join(names[k] for k in toggle_platform(job_id, key))


def send_for_approval(job_id: str, video: Path, caption: str, cover: Path | None = None) -> int:
    size = video.stat().st_size
    if size > TG_MAX_UPLOAD:
        raise TelegramError(f"{video.name} is {size/1e6:.1f} MB; Telegram bots can upload 50 MB max")
    markup = approval_markup(job_id)
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


# chat id -> (what is being edited, job id, when): the next message from the owner answers it
_pending: dict[int, tuple[str, str, float]] = {}
PENDING_TTL = 15 * 60


def _clip_options(job_id: str, key: str) -> list[str]:
    from app.utils.job_store import get_store

    job = get_store().get(job_id) or {}
    return list(((job.get("meta") or {}).get("llm") or {}).get(key) or [])


def _download_photo(photo_sizes: list[dict]) -> bytes:
    best = max(photo_sizes, key=lambda p: p.get("file_size") or p.get("width", 0))
    f = _api("getFile", data={"file_id": best["file_id"]})
    token = get_settings().telegram_bot_token
    r = httpx.get(f"https://api.telegram.org/file/bot{token}/{f['file_path']}", timeout=60)
    r.raise_for_status()
    return r.content


def _ask(chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> None:
    data = {"chat_id": chat_id, "text": text}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    _api("sendMessage", data=data)


def _edit_callback(action: str, rest: str, chat_id: int, on_edit: Callable[..., str]) -> str:
    job_id, _, idx = rest.partition(":")
    if action == "et":
        opts = _clip_options(job_id, "title_options")
        _pending[chat_id] = ("title", job_id, time.time())
        _ask(chat_id, "✏️ Pick a title, or just type your own:",
             [[{"text": t[:60], "callback_data": f"ts:{job_id}:{i}"}] for i, t in enumerate(opts[:3])] or None)
        return "asked for title"
    if action == "eh":
        opts = _clip_options(job_id, "hook_options")
        _pending[chat_id] = ("hook", job_id, time.time())
        _ask(chat_id, "🪝 Pick a hook (the big text in the first seconds), or type your own. Send 'none' to remove it:",
             [[{"text": h[:60], "callback_data": f"hs:{job_id}:{i}"}] for i, h in enumerate(opts[:3])] or None)
        return "asked for hook"
    if action == "ec":
        _pending[chat_id] = ("cover", job_id, time.time())
        _ask(chat_id, "🖼 Change the cover. Send one of:\n• a photo to use as the cover\n"
                      "• a number of seconds (e.g. 12) to use that moment of the clip\n• text to write on the cover",
             [[{"text": "↩️ Back to automatic cover", "callback_data": f"cr:{job_id}"}]])
        return "asked for cover"
    _pending.pop(chat_id, None)
    if action == "ts":
        opts = _clip_options(job_id, "title_options")
        return on_edit(job_id, title=opts[int(idx)]) if idx.isdigit() and int(idx) < len(opts) else "option gone"
    if action == "hs":
        opts = _clip_options(job_id, "hook_options")
        return on_edit(job_id, hook=opts[int(idx)]) if idx.isdigit() and int(idx) < len(opts) else "option gone"
    if action == "cr":
        return on_edit(job_id, cover_reset=True)
    return "unknown"


def _answer_pending(msg: dict, on_edit: Callable[..., str]) -> str | None:
    chat_id = msg["chat"]["id"]
    p = _pending.get(chat_id)
    if not p or time.time() - p[2] > PENDING_TTL:
        _pending.pop(chat_id, None)
        return None
    what, job_id, _ = p
    text = (msg.get("text") or msg.get("caption") or "").strip()
    try:
        if what == "cover" and msg.get("photo"):
            result = on_edit(job_id, cover_image=_download_photo(msg["photo"]))
        elif not text:
            return None
        elif what == "title":
            result = on_edit(job_id, title=text)
        elif what == "hook":
            result = on_edit(job_id, hook="" if text.lower() in ("none", "no", "remove") else text)
        else:
            try:
                result = on_edit(job_id, cover_time=float(text.rstrip("s")))
            except ValueError:
                result = on_edit(job_id, cover_text=text)
    except (ValueError, TelegramError, httpx.HTTPError) as e:
        result = f"Couldn't change it: {e}"
    _pending.pop(chat_id, None)
    send_text(result, chat_id=chat_id)
    return result


HELP = ("Send me a video or live-stream link and I'll make clips. Add format words to choose the shape, e.g.\n"
        "https://youtu.be/... landscape square\n"
        "Formats: vertical (9:16), portrait (4:5), square (1:1), landscape (16:9).\n\n"
        "/check <link> — copyright and monetisation risk of a video before you clip it\n"
        "Under each clip: ✏️ Title, 🪝 Hook and 🖼 Cover let you change them, and the ☑️ platform buttons "
        "choose where it gets posted (all connected platforms are ticked; tap one to untick it; 🔒 = not connected yet).")


def handle_update(update: dict, on_approve: Callable[[str], str], on_reject: Callable[[str], str],
                  on_link: Callable[[str], str], on_edit: Callable[..., str] | None = None,
                  on_check: Callable[[str], str] | None = None) -> str | None:
    """Shared by the webhook endpoint and the long-poller. Returns a short status for logging."""
    owner = get_settings().telegram_owner_chat_id
    cq = update.get("callback_query")
    if cq:
        if int(cq.get("from", {}).get("id", 0)) != owner:
            return "ignored: not owner"
        action, _, rest = (cq.get("data") or "").partition(":")
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id") or owner
        final = action in ("a", "r", "f")
        try:
            if action == "a":
                result = on_approve(rest)
                if result.startswith("Scheduled in"):  # spaced after a similar clip: offer "post now anyway"
                    try:
                        _api("sendMessage", data={"chat_id": chat_id, "text": "⏳ " + result,
                                                  "reply_markup": json.dumps({"inline_keyboard": [[
                                                      {"text": "🚀 Publish now anyway", "callback_data": f"f:{rest}"}]]})})
                    except TelegramError:
                        pass
            elif action == "f":
                result = on_approve(rest, force=True)
            elif action == "r":
                result = on_reject(rest)
            elif action == "p":
                result = _toggle_platform(rest)
            elif on_edit is not None:
                result = _edit_callback(action, rest, chat_id, on_edit)
            else:
                result = "unknown"
        except (ValueError, TelegramError) as e:
            result = f"Couldn't do that: {e}"
        try:
            _api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": result[:190]})
            if msg and final:
                _api("editMessageReplyMarkup", data={"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                                     "reply_markup": json.dumps({"inline_keyboard": []})})
            elif msg and action == "p" and result.startswith("Will post"):
                _api("editMessageReplyMarkup", data={
                    "chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                    "reply_markup": json.dumps(approval_markup(rest.partition(":")[0]))})
            elif action in ("ts", "hs", "cr"):
                send_text(result, chat_id=chat_id)
        except TelegramError as e:  # stale callbacks can't be answered; not fatal
            log.warning("telegram ack failed: %s", e)
        return result
    msg = update.get("message") or {}
    if msg and int(msg.get("from", {}).get("id", 0)) == owner:
        from app.services.ingest import extract_url

        text = msg.get("text") or msg.get("caption") or ""
        low = text.strip().lower()
        if low in ("/start", "/help"):
            send_text(HELP, chat_id=msg["chat"]["id"])
            return "help"
        url = extract_url(text)
        if low.startswith("/check"):
            if not url:
                send_text("Send it like this: /check https://youtu.be/...", chat_id=msg["chat"]["id"])
                return "check: no url"
            result = on_check(url) if on_check else "Copyright check isn't available"
            send_text(result, chat_id=msg["chat"]["id"])
            return "check"
        if on_edit is not None and not url:
            answered = _answer_pending(msg, on_edit)
            if answered is not None:
                return answered
        if url:
            _pending.pop(msg["chat"]["id"], None)
            result = on_link(url, text)
            send_text(result, chat_id=msg["chat"]["id"])
            return result
    return None


def poll_forever(on_approve, on_reject, on_link, stop: Callable[[], bool] = lambda: False,
                 on_edit=None, on_check=None) -> None:
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
                    log.info("update %s -> %s", u["update_id"], handle_update(u, on_approve, on_reject, on_link,
                                                                                on_edit, on_check))
                except Exception:  # never let one bad update kill the poller
                    log.exception("update handling failed")
