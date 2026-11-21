"""Optional LLM (local: Ollama, LM Studio, llama.cpp; or cloud: Claude, ChatGPT, Gemini, Kimi, ...) for the parts of
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


# Every provider is either Ollama's own API, Anthropic's Messages API, or the
# OpenAI-compatible /chat/completions API (which most local servers and most
# cloud providers speak). Cloud providers need an API key from their console:
# the consumer chat apps (claude.ai, chatgpt.com, gemini.google.com) can't be
# driven by another program, so the key is the supported way in.
PROVIDERS: dict[str, dict[str, Any]] = {
    "none": {"label": "No LLM", "kind": "none", "url": "", "local": True},
    "ollama": {"label": "Ollama (local)", "kind": "ollama", "url": "http://localhost:11434", "local": True},
    "lmstudio": {"label": "LM Studio (local)", "kind": "openai", "url": "http://localhost:1234/v1", "local": True},
    "openai": {"label": "Other local server (llama.cpp, Jan, vLLM, LocalAI)", "kind": "openai",
               "url": "http://localhost:8080/v1", "local": True},
    "claude": {"label": "Claude (Anthropic API)", "kind": "anthropic", "url": "https://api.anthropic.com",
               "key_url": "https://console.anthropic.com/settings/keys",
               "models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]},
    "chatgpt": {"label": "ChatGPT (OpenAI API)", "kind": "openai", "url": "https://api.openai.com/v1",
                "key_url": "https://platform.openai.com/api-keys", "models": ["gpt-4o-mini", "gpt-4o"]},
    "gemini": {"label": "Gemini (Google AI Studio, has a free tier)", "kind": "openai",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_url": "https://aistudio.google.com/apikey", "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
    "kimi": {"label": "Kimi (Moonshot AI)", "kind": "openai", "url": "https://api.moonshot.ai/v1",
             "key_url": "https://platform.moonshot.ai/console/api-keys", "models": ["kimi-k2-turbo-preview"]},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "url": "https://api.deepseek.com/v1",
                 "key_url": "https://platform.deepseek.com/api_keys", "models": ["deepseek-chat"]},
    "groq": {"label": "Groq (fast, has a free tier)", "kind": "openai", "url": "https://api.groq.com/openai/v1",
             "key_url": "https://console.groq.com/keys", "models": ["llama-3.3-70b-versatile"]},
    "openrouter": {"label": "OpenRouter (many models, some free)", "kind": "openai",
                   "url": "https://openrouter.ai/api/v1", "key_url": "https://openrouter.ai/keys", "models": []},
    "mistral": {"label": "Mistral", "kind": "openai", "url": "https://api.mistral.ai/v1",
                "key_url": "https://console.mistral.ai/api-keys", "models": ["mistral-small-latest"]},
}
ANTHROPIC_VERSION = "2023-06-01"


def kind(provider: str) -> str:
    return PROVIDERS.get(provider, PROVIDERS["openai"])["kind"]


def config() -> dict[str, Any]:
    return runtime_settings.load()


def enabled(cfg: dict | None = None) -> bool:
    cfg = cfg or config()
    return cfg["llm_provider"] != "none" and bool(cfg["llm_model"])


def _base(cfg: dict) -> str:
    return (cfg.get("llm_base_url") or PROVIDERS.get(cfg["llm_provider"], {}).get("url", "")).rstrip("/")


def _anthropic_headers(key: str) -> dict:
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}


def list_models(provider: str, base_url: str, api_key: str = "", client: httpx.Client | None = None) -> list[str]:
    c = client or httpx.Client(timeout=15)
    base = (base_url or PROVIDERS.get(provider, {}).get("url", "")).rstrip("/")
    k = kind(provider)
    try:
        if k == "ollama":
            r = c.get(f"{base}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        if k == "anthropic":
            if not api_key:
                return list(PROVIDERS[provider].get("models", []))
            r = c.get(f"{base}/v1/models", headers=_anthropic_headers(api_key), params={"limit": 100})
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        if k == "openai":
            if not api_key and not PROVIDERS.get(provider, {}).get("local", provider not in PROVIDERS):
                return list(PROVIDERS[provider].get("models", []))
            h = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = c.get(f"{base}/models", headers=h)
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
            return sorted(i.removeprefix("models/") for i in ids)
        return []
    except httpx.HTTPError as e:
        raise LLMError(f"can't reach {PROVIDERS.get(provider, {}).get('label', provider)} at {base}: {e}") from e
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
    key = runtime_settings.api_key(cfg)
    k = kind(cfg["llm_provider"])
    c = client or httpx.Client(timeout=timeout)
    try:
        if k == "ollama":
            r = c.post(f"{_base(cfg)}/api/chat", json={"model": cfg["llm_model"], "messages": msgs, "stream": False,
                                                       "format": "json", "options": {"temperature": 0.7}})
            r.raise_for_status()
            text = r.json()["message"]["content"]
        elif k == "anthropic":
            if not key:
                raise LLMError("Claude needs an API key (console.anthropic.com)")
            r = c.post(f"{_base(cfg)}/v1/messages", headers=_anthropic_headers(key), json={
                "model": cfg["llm_model"], "max_tokens": 1500, "temperature": 0.7,
                "system": system + " Reply with the JSON object only.",
                "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        else:
            h = {"Authorization": f"Bearer {key}"} if key else {}
            body = {"model": cfg["llm_model"], "messages": msgs, "temperature": 0.7,
                    "response_format": {"type": "json_object"}}
            r = c.post(f"{_base(cfg)}/chat/completions", headers=h, json=body)
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format")  # some servers don't support JSON mode; the prompt asks for JSON anyway
                r = c.post(f"{_base(cfg)}/chat/completions", headers=h, json=body)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        raise LLMError(f"LLM request failed ({e.response.status_code}): {e.response.text[:200]}") from e
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
    "You are a viral short-form video editor and YouTube SEO expert (YouTube Shorts, Instagram Reels, TikTok). "
    "From what is said in a clip (with [seconds] markers), write:\n"
    "HOOK: 3-7 words shown big on screen in the first seconds. Curiosity gap, tension or a bold claim that the clip "
    "pays off ('He did NOT expect this', 'Wait for the ending', 'Worst mistake ever?'). No spoilers.\n"
    "TITLE: under 60 characters, hook-first, with the most searched keyword (the person, game or show name) in the "
    "first 40 characters, at most one emoji, no lies.\n"
    "CAPTION: one punchy line ending with a question that makes people comment.\n"
    "DESCRIPTION: 2-3 sentences describing what happens, naturally using the words people search for (names, game "
    "or show, the event, 'funny moments', 'reaction', 'highlights').\n"
    "SEARCH_PHRASES: 8-12 phrases people actually type into YouTube search to find this (e.g. 'kai cenat gta 6', "
    "'gta 6 funny moments').\n"
    "HASHTAGS: 10-15, most specific first (names, game/show), then niche, then broad (#gaming, #funny).\n"
    "KEYWORDS: 2-5 words from the transcript worth highlighting in captions.\n"
    "PAYOFF_SECOND: the second (from the markers) where the funniest, most shocking or most satisfying moment "
    "happens, or null if you can't tell.\n"
    "VIRALITY: your honest 0-100 estimate of how this clip would perform on its own.\n"
    "Also 2 ALT_HOOKS and 2 ALT_TITLES in different styles (question, bold claim, emotion) for A/B tests.\n"
    "Reply with JSON only: {\"hook\": str, \"title\": str, \"alt_hooks\": [str], \"alt_titles\": [str], "
    "\"caption\": str, \"description\": str, \"search_phrases\": [str], \"hashtags\": [str], "
    "\"keywords\": [str], \"payoff_second\": number|null, \"virality\": int}."
)


def _str_list(v, n: int) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    return [" ".join(str(x).strip().strip('"').split()) for x in (v or []) if str(x).strip()][:n]


def clip_metadata(transcript: str, source_title: str, fmt: str, cfg: dict | None = None,
                  client: httpx.Client | None = None, avoid: list[str] | None = None) -> dict:
    avoid_txt = ("Other clips from this video already use these hooks/titles, so write different ones: "
                 + "; ".join(a for a in avoid if a) + "\n") if avoid else ""
    user = (avoid_txt + f"Source video title: {source_title or 'unknown'}\n"
            f"Clip format: {fmt}\n"
            f"What is said in the clip (may be empty if it's all action):\n{transcript[:3500] or '(no speech)'}")
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
    try:
        payoff = float(d.get("payoff_second")) if d.get("payoff_second") is not None else None
    except (TypeError, ValueError):
        payoff = None

    def short_hook(x) -> str:
        return " ".join(str(x or "").strip().strip('"').split()[:9])

    hook = short_hook(d.get("hook"))
    alt_h = d.get("alt_hooks") or []
    alt_t = d.get("alt_titles") or []
    hooks = list(dict.fromkeys(h for h in [hook, *(short_hook(x) for x in (alt_h if isinstance(alt_h, list) else []))] if h))
    titles = list(dict.fromkeys(t for t in [title, *(str(x).strip().strip('"')[:100] for x in
                                                     (alt_t if isinstance(alt_t, list) else []))] if t))
    return {"title": title, "hook": hook, "hook_options": hooks[:3], "title_options": titles[:3],
            "caption": str(d.get("caption") or "").strip()[:1500],
            "description": str(d.get("description") or "").strip()[:1500],
            "search_phrases": _str_list(d.get("search_phrases"), 12),
            "hashtags": tags[:15], "keywords": [str(k).strip() for k in kws if str(k).strip()][:6],
            "payoff_second": payoff, "virality": vir}


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
