"""Optional local LLM (Ollama or any OpenAI-compatible server) for the parts of
clipping that need language understanding:

  * write a hook-style title, a caption and hashtags from what is said in the clip
  * help choose which candidate moments become clips (transcript + audio score)

Everything stays on your machine when the server is local. With provider "none",
or if the server is unreachable, the pipeline falls back to the non-LLM behaviour.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.utils import runtime_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def config() -> dict[str, Any]:
    return runtime_settings.load()


def enabled(cfg: dict | None = None) -> bool:
    cfg = cfg or config()
    return cfg["llm_provider"] != "none" and bool(cfg["llm_model"])


def _base(cfg: dict) -> str:
    return cfg["llm_base_url"].rstrip("/")


def list_models(provider: str, base_url: str, api_key: str = "", client: httpx.Client | None = None) -> list[str]:
    c = client or httpx.Client(timeout=10)
    try:
        if provider == "ollama":
            r = c.get(f"{base_url.rstrip('/')}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        if provider == "openai":
            h = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = c.get(f"{base_url.rstrip('/')}/models", headers=h)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        return []
    except httpx.HTTPError as e:
        raise LLMError(f"can't reach {provider} at {base_url}: {e}") from e
    finally:
        if client is None:
            c.close()


def chat_json(system: str, user: str, cfg: dict | None = None, client: httpx.Client | None = None,
              timeout: float = 180) -> dict:
    """Ask for a JSON object and parse it (tolerates code fences / extra prose)."""
    cfg = cfg or config()
    if not enabled(cfg):
        raise LLMError("no LLM configured")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    c = client or httpx.Client(timeout=timeout)
    try:
        if cfg["llm_provider"] == "ollama":
            r = c.post(f"{_base(cfg)}/api/chat", json={"model": cfg["llm_model"], "messages": msgs, "stream": False,
                                                       "format": "json", "options": {"temperature": 0.7}})
            r.raise_for_status()
            text = r.json()["message"]["content"]
        else:
            h = {"Authorization": f"Bearer {cfg['llm_api_key']}"} if cfg.get("llm_api_key") else {}
            r = c.post(f"{_base(cfg)}/chat/completions", headers=h, json={
                "model": cfg["llm_model"], "messages": msgs, "temperature": 0.7,
                "response_format": {"type": "json_object"}})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as e:
        raise LLMError(f"LLM request failed: {e}") from e
    finally:
        if client is None:
            c.close()
    return parse_json(text)


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise LLMError(f"LLM did not return JSON: {text[:200]}")


META_SYSTEM = (
    "You are a viral short-form video editor (YouTube Shorts, Instagram Reels, TikTok). From what is said in a clip, "
    "write: a HOOK of 3-7 words shown big on screen in the first 3 seconds (curiosity gap, tension or a bold claim, "
    "e.g. 'He did NOT expect this', 'Wait for the ending'); a TITLE under 70 characters that makes people tap "
    "(no lies, at most one emoji); a CAPTION of 1-2 short sentences ending with a question or call to comment; "
    "3-6 relevant HASHTAGS; 2-5 KEYWORDS from the transcript worth highlighting in the captions; and VIRALITY, "
    "your honest 0-100 estimate of how well this clip would perform on its own. "
    "Reply with JSON only: {\"hook\": str, \"title\": str, \"caption\": str, \"hashtags\": [str], "
    "\"keywords\": [str], \"virality\": int}."
)


def clip_metadata(transcript: str, source_title: str, fmt: str, cfg: dict | None = None,
                  client: httpx.Client | None = None) -> dict:
    user = (f"Source video title: {source_title or 'unknown'}\n"
            f"Clip format: {fmt}\n"
            f"What is said in the clip (may be empty if it's all action):\n{transcript[:3000] or '(no speech)'}")
    d = chat_json(META_SYSTEM, user, cfg, client)
    title = str(d.get("title") or "").strip().strip('"')[:100]
    tags = d.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = [t if t.startswith("#") else f"#{t}" for t in (re.sub(r"\s+", "", str(t)) for t in tags) if t.strip("#")]
    kws = d.get("keywords") or []
    if isinstance(kws, str):
        kws = kws.split(",")
    try:
        vir = max(0, min(100, int(float(d.get("virality")))))
    except (TypeError, ValueError):
        vir = None
    hook = " ".join(str(d.get("hook") or "").strip().strip('"').split()[:9])
    return {"title": title, "hook": hook, "caption": str(d.get("caption") or "").strip()[:1500],
            "hashtags": tags[:8], "keywords": [str(k).strip() for k in kws if str(k).strip()][:6], "virality": vir}


RANK_SYSTEM = (
    "You pick the moments most likely to go viral as short clips. You get numbered candidate moments, each with "
    "an audio excitement score and what is said. Favour strong hooks, funny or surprising lines, reactions and "
    "clear payoffs. Reply with JSON only: {\"scores\": [{\"id\": int, \"score\": 0-10}]}."
)


def rank_moments(candidates: list[dict], source_title: str, cfg: dict | None = None,
                 client: httpx.Client | None = None) -> dict[int, float]:
    lines = [f"#{c['id']} ({c['start']:.0f}-{c['end']:.0f}s, excitement {c['score']:+.2f}): "
             f"{(c.get('transcript') or '(no speech)')[:500]}" for c in candidates]
    d = chat_json(RANK_SYSTEM, f"Video: {source_title or 'unknown'}\n\n" + "\n".join(lines), cfg, client)
    out = {}
    for item in d.get("scores", []):
        try:
            out[int(item["id"])] = max(0.0, min(10.0, float(item["score"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out
