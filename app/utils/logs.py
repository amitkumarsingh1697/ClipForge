"""Keep secrets out of log files.

httpx logs every request URL at INFO, and Telegram puts the bot token in the
URL (https://api.telegram.org/bot<TOKEN>/sendMessage), so data/serve.log would
contain the token. Request logs are turned down, and anything that still looks
like a bot token or an access_token query parameter is masked.
"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(api\.telegram\.org/(?:file/)?bot)[0-9]+:[A-Za-z0-9_-]+"), r"\1***"),
    (re.compile(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b"), "***bot-token***"),
    (re.compile(r"((?:access_token|client_secret|refresh_token|key)=)[^&\s\"']+", re.I), r"\1***"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def quiet_http_logs() -> None:
    for name in ("httpx", "httpcore", "urllib3", "googleapiclient.discovery"):
        logging.getLogger(name).setLevel(logging.WARNING)
    f = RedactFilter()
    root = logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(x, RedactFilter) for x in h.filters):
            h.addFilter(f)
