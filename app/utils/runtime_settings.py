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
    "llm_provider": "none",                    # see app.services.llm.PROVIDERS
    "llm_base_url": "http://localhost:11434",  # Ollama default; LM Studio is http://localhost:1234/v1
    "llm_model": "",
    "llm_api_key": "",                         # legacy single key (used when the provider has none in llm_keys)
    "llm_keys": {},                            # provider -> API key (cloud providers)
    "llm_titles": True,                        # write title, caption and hashtags from the transcript
    "llm_rank": False,                         # let the LLM help pick which moments become clips (slower)
    "whisper_model": None,                     # None = WHISPER_MODEL from .env
    "watermark": "",                           # your handle drawn on every clip, e.g. @peakmoments
    "default_hashtags": "",                    # added when the LLM gives none
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
        from app.services.llm import PROVIDERS

        key = changes.pop("llm_api_key", None)
        for k, v in changes.items():
            if k not in DEFAULTS:
                raise ValueError(f"unknown setting {k!r}")
            data[k] = v
        if data["llm_provider"] not in PROVIDERS:
            raise ValueError(f"llm_provider must be one of {', '.join(PROVIDERS)}")
        if key is not None:
            keys = dict(data.get("llm_keys") or {})
            if key:
                keys[data["llm_provider"]] = key
            else:
                keys.pop(data["llm_provider"], None)
            data["llm_keys"] = keys
            data["llm_api_key"] = ""
        if data["whisper_model"] not in WHISPER_MODELS:
            raise ValueError(f"whisper_model must be one of {', '.join(WHISPER_MODELS)}")
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
        return data


def api_key(data: dict[str, Any], provider: str | None = None) -> str:
    provider = provider or data.get("llm_provider", "none")
    return (data.get("llm_keys") or {}).get(provider) or data.get("llm_api_key") or ""


def public(data: dict[str, Any]) -> dict[str, Any]:
    """What the UI may see: API keys are reported as set/unset only."""
    from app.services.llm import PROVIDERS

    out = {k: v for k, v in data.items() if k != "llm_keys"}
    out["llm_api_key"] = bool(api_key(data))
    out["llm_keys_set"] = sorted(k for k, v in (data.get("llm_keys") or {}).items() if v)
    out["whisper_models"] = WHISPER_MODELS
    out["providers"] = {k: {kk: vv for kk, vv in v.items()} for k, v in PROVIDERS.items()}
    return out
