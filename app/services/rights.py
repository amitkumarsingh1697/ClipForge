"""Copyright and monetisation risk check for a source video.

This is a heuristic, not a legal opinion. No platform publishes, for a given
video, who owns every part of it or whether it earns ad money, so the check
reads what the link's public metadata does reveal (licence, channel size and
verification, category, music credits, who uploaded it, what the title says)
and turns that into a risk level with the reasons and what to do about it.

  risk        low | medium | high | unknown
  monetised   likely | unlikely | unknown   (is the original likely earning ad money)
"""
from __future__ import annotations

import re
from typing import Any

RIGHTS_FIELDS = ("title", "uploader", "uploader_id", "channel", "channel_id", "channel_follower_count",
                 "channel_is_verified", "license", "categories", "tags", "track", "artist", "album",
                 "creator", "extractor", "extractor_key", "is_live", "was_live", "age_limit",
                 "availability", "view_count", "webpage_url")

# Channels whose uploads are almost always Content ID-protected.
RIGHTS_HOLDERS = re.compile(
    r"\b(vevo|records|recordings|music|official|tv|network|studios?|pictures|entertainment|films?|media group|"
    r"crunchyroll|toei|funimation|aniplex|viz|netflix|disney|marvel|hbo|warner|sony|universal|paramount|"
    r"t-series|zee|star plus|colors tv|sun tv|nba|nfl|nhl|mlb|fifa|uefa|ufc|wwe|premier league|laliga|"
    r"espn|sky sports|bbc|cnn|fox|nbc|abc|cbs|ipl|bcci|icc|formula 1|f1)\b", re.I)
TV_FILM = re.compile(r"\b(full (episode|movie|match)|episode \d+|ep\.? ?\d+|season \d+|official (trailer|video)|"
                     r"music video|lyrics?|full album|ost|soundtrack|highlights? \| )\b", re.I)
STREAM_SITES = ("twitch", "kick")
SOCIAL_SITES = ("tiktok", "instagram", "twitter", "x", "facebook", "reddit", "snapchat")


def _level(score: int) -> str:
    return "low" if score < 30 else "medium" if score < 60 else "high"


def assess(info: dict[str, Any] | None, source_type: str = "url") -> dict[str, Any]:
    info = info or {}
    reasons: list[str] = []
    advice: list[str] = []
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    who = " ".join(str(info.get(k) or "") for k in ("channel", "uploader", "uploader_id", "creator"))
    title = str(info.get("title") or "")
    cats = [str(c).lower() for c in (info.get("categories") or [])]
    followers = info.get("channel_follower_count")
    licence = str(info.get("license") or "")

    if source_type == "upload" and not who.strip():
        reasons.append("Uploaded file: ClipForge can't see who owns it. Only post it if you made it or have permission.")
        if RIGHTS_HOLDERS.search(title) or TV_FILM.search(title):
            reasons.append(f"The file name ('{title[:60]}') looks like TV, film, sports or music content.")
            return {"risk": "high", "score": 75, "monetised": "unknown", "reasons": reasons,
                    "advice": _advice("high", music=False)}
        return {"risk": "unknown", "score": None, "monetised": "unknown", "reasons": reasons,
                "advice": _advice("unknown", music=False)}

    score = 35
    if "creative commons" in licence.lower():
        score -= 40
        reasons.append(f"Licensed {licence}: reuse is allowed if you credit the creator.")
    music = "music" in cats or bool(info.get("track") or info.get("artist") or info.get("album"))
    if music:
        score += 40
        reasons.append("It contains a credited music track. Music is matched by Content ID almost every time.")
    if RIGHTS_HOLDERS.search(who):
        score += 35
        reasons.append(f"Uploaded by '{who.split()[0] if who.split() else who}', which looks like a media company, "
                       "label or league. They usually claim or block re-uploads.")
    if TV_FILM.search(title):
        score += 20
        reasons.append("The title looks like TV, film, a music video or official sports footage.")
    if info.get("channel_is_verified"):
        score += 10
        reasons.append("The channel is verified.")
    if isinstance(followers, (int, float)):
        if followers >= 1_000_000:
            score += 15
            reasons.append(f"Big channel ({followers/1e6:.1f}M followers): more likely to use Content ID.")
        elif followers >= 100_000:
            score += 8
            reasons.append(f"{followers/1e3:.0f}K followers.")
    if any(s in extractor for s in STREAM_SITES) or info.get("is_live") or info.get("was_live"):
        score += 5
        reasons.append("Live stream / streamer content. Many streamers welcome clips (some pay clippers), "
                       "but check their rules, and background music can still be claimed.")
    elif any(extractor.startswith(s) for s in SOCIAL_SITES):
        score += 10
        reasons.append("Social post: the original creator owns it, and these platforms act on reports.")
    if "gaming" in cats:
        score -= 5
        reasons.append("Gaming: most publishers allow gameplay videos, though cutscenes and soundtracks can be claimed.")
    if (info.get("age_limit") or 0) >= 18:
        score += 10
        reasons.append("Age-restricted content.")
    score = max(0, min(100, score))
    if not reasons:
        reasons.append("No strong signals either way. The original creator still owns the video.")

    if "youtube" in extractor:
        if isinstance(followers, (int, float)) and followers >= 1000:
            monetised = "likely"
            reasons.append("The channel is big enough for the YouTube Partner Program, so the video is probably "
                           "earning ads. YouTube doesn't publish this per video.")
        elif isinstance(followers, (int, float)):
            monetised = "unlikely"
        else:
            monetised = "unknown"
    else:
        monetised = "likely" if isinstance(followers, (int, float)) and followers >= 10_000 else "unknown"
    level = _level(score)
    return {"risk": level, "score": score, "monetised": monetised, "reasons": reasons,
            "advice": _advice(level, music)}


def _advice(level: str, music: bool) -> list[str]:
    tips = []
    if level in ("high", "unknown"):
        tips.append("Get permission, or clip creators who run a clipping programme (Whop Content Rewards, Vyro "
                    "and similar pay for it).")
    if music:
        tips.append("Mute or replace the music before posting.")
    tips.append("Transform it: your own hook, commentary or voice-over makes it far less likely to be treated "
                "as 'reused content' by YouTube's monetisation review.")
    tips.append("Credit the original creator in the caption.")
    return tips


def summary_line(r: dict | None) -> str:
    if not r:
        return ""
    icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(r.get("risk"), "⚪")
    mon = {"likely": "original is probably monetised", "unlikely": "original probably isn't monetised"}.get(
        r.get("monetised"), "monetisation unknown")
    return f"{icon} Copyright risk: {r.get('risk', 'unknown')} ({mon})"


def check_url(url: str) -> dict[str, Any]:
    from app.services.ingest import probe_url

    info = probe_url(url)
    st = "live" if info.get("is_live") else "url"
    out = assess({k: info.get(k) for k in RIGHTS_FIELDS}, st)
    out["title"] = info.get("title")
    out["uploader"] = info.get("channel") or info.get("uploader")
    out["is_live"] = bool(info.get("is_live"))
    return out
