"""Settings the user changes from the web UI (stored in <data_dir>/settings.json).

.env holds secrets and deployment choices; this file holds preferences that are
safe to edit from the browser, such as which local LLM and Whisper model to use.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from app.config import get_settings

DEFAULTS: dict[str, Any] = {
    "llm_provider": "none",                    # none | ollama | openai (any OpenAI-compatible server: LM Studio, llama.cpp, vLLM)
    "llm_base_url": "http://localhost:11434",  # Ollama default; LM Studio is http://localhost:1234/v1
    "llm_model": "",
    "llm_api_key": "",                         # only for servers that require one
    "llm_titles": True,                        # write title, caption and hashtags from the transcript
    "llm_rank": False,                         # let the LLM help pick which moments become clips (slower)
    "whisper_model": None,                     # None = WHISPER_MODEL from .env
}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
_lock = threading.Lock()


def _path():
    return get_settings().data_dir / "settings.json"


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    p = _path()
    if p.exists():
        try:
            data.update({k: v for k, v in json.loads(p.read_text()).items() if k in DEFAULTS})
        except (ValueError, OSError):
            pass
    if not data["whisper_model"]:
        data["whisper_model"] = get_settings().whisper_model
    return data


def save(changes: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        data = load()
        for k, v in changes.items():
            if k not in DEFAULTS:
                raise ValueError(f"unknown setting {k!r}")
            data[k] = v
        if data["llm_provider"] not in ("none", "ollama", "openai"):
            raise ValueError("llm_provider must be none, ollama or openai")
        if data["whisper_model"] not in WHISPER_MODELS:
            raise ValueError(f"whisper_model must be one of {', '.join(WHISPER_MODELS)}")
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
        return data


def public(data: dict[str, Any]) -> dict[str, Any]:
    """What the UI may see: the API key is reported as set/unset only."""
    out = dict(data)
    out["llm_api_key"] = bool(data.get("llm_api_key"))
    out["whisper_models"] = WHISPER_MODELS
    return out
