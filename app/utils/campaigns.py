"""Clipping campaigns: creators and brands that pay per view for clips.

Marketplaces such as Whop Content Rewards, Vyro, ClipAffiliates and
Promote.fun have no public API for submitting clips. The flow on all of them
is: join a campaign, post the clip on your own TikTok / Shorts / Reels with the
campaign's required tags, then submit the post link on their site. ClipForge
keeps the campaigns you joined, adds their required hashtags and mentions to
the caption automatically, keeps each published post's link ready to submit,
and estimates earnings from the views it can read (YouTube, Instagram).
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.config import get_settings

MARKETPLACES: dict[str, dict[str, str]] = {
    "whop": {"name": "Whop Content Rewards", "url": "https://whop.com",
             "how": "Join a campaign, post, then submit your post link on Whop. Paid per 1K views."},
    "contentrewards": {"name": "Content Rewards", "url": "https://contentrewards.com",
                       "how": "Pick a campaign, post with its rules, submit the link."},
    "vyro": {"name": "Vyro", "url": "https://vyro.com",
             "how": "Post on TikTok, Shorts, Reels or X and submit the link; views count from submission."},
    "clipaffiliates": {"name": "ClipAffiliates", "url": "https://www.clipaffiliates.com",
                       "how": "Per-campaign rates; submit post links."},
    "promote": {"name": "Promote.fun", "url": "https://promote.fun",
                "how": "Campaigns pay per 1K views; submit your post links."},
    "clipster": {"name": "Clipster", "url": "https://www.clipster.com",
                 "how": "Campaign-set pay; withdraw via PayPal, bank, USDT or gift cards ($50 minimum)."},
    "clippingnet": {"name": "Clipping.net", "url": "https://clipping.net",
                    "how": "Per-1K-view campaigns; submit your post links."},
    "kick": {"name": "Kick Bounties", "url": "https://bounties.kick.com",
             "how": "Fixed rewards for clip challenges from Kick streamers (not per view)."},
    "direct": {"name": "Direct deal with a creator", "url": "",
               "how": "A streamer or podcaster pays you directly (per clip or per view)."},
    "other": {"name": "Other", "url": "", "how": ""},
}
_lock = threading.Lock()


def _path():
    return get_settings().data_dir / "campaigns.json"


def load() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return []


def _save(items: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2))


def get(cid: str | None) -> dict | None:
    return next((c for c in load() if c["id"] == cid), None) if cid else None


def upsert(data: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        items = load()
        c = next((x for x in items if x["id"] == data.get("id")), None)
        if c is None:
            c = {"id": uuid.uuid4().hex[:8], "active": True}
            items.append(c)
        for k in ("name", "marketplace", "url", "cpm", "hashtags", "mention", "notes", "active", "source_url"):
            if k in data and data[k] is not None:
                c[k] = data[k]
        if c.get("marketplace") not in MARKETPLACES:
            c["marketplace"] = "other"
        try:
            c["cpm"] = max(0.0, float(c.get("cpm") or 0))
        except (TypeError, ValueError):
            c["cpm"] = 0.0
        if not c.get("name"):
            c["name"] = MARKETPLACES[c["marketplace"]]["name"]
        _save(items)
        return c


def delete(cid: str) -> bool:
    with _lock:
        items = load()
        rest = [c for c in items if c["id"] != cid]
        _save(rest)
        return len(rest) != len(items)


def caption_extras(cid: str | None) -> str:
    """Required hashtags / @mentions of the campaign, appended to the post caption."""
    c = get(cid)
    if not c:
        return ""
    parts = []
    for t in str(c.get("hashtags") or "").replace(",", " ").split():
        parts.append(t if t.startswith("#") else f"#{t}")
    for m in str(c.get("mention") or "").replace(",", " ").split():
        parts.append(m if m.startswith("@") else f"@{m}")
    return " ".join(parts)


def post_links(meta: dict) -> list[dict[str, str]]:
    out = []
    if meta.get("youtube_id"):
        out.append({"platform": "YouTube", "url": f"https://youtube.com/shorts/{meta['youtube_id']}"
                    if meta.get("youtube_short") else f"https://youtu.be/{meta['youtube_id']}"})
    if meta.get("instagram_permalink") or meta.get("instagram_id"):
        out.append({"platform": "Instagram", "url": meta.get("instagram_permalink") or f"media {meta['instagram_id']}"})
    if meta.get("facebook_id"):
        out.append({"platform": "Facebook", "url": f"https://www.facebook.com/reel/{meta['facebook_id']}"})
    if meta.get("tiktok_id"):
        out.append({"platform": "TikTok", "url": meta.get("tiktok_url") or f"publish id {meta['tiktok_id']}"})
    return out


def estimate(views: int, cpm: float) -> float:
    return round((views or 0) / 1000 * (cpm or 0), 2)
