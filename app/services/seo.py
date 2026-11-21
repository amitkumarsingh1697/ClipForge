"""Search & discovery packaging: titles, descriptions, hashtags and tags per platform.

What the platforms actually use (checked Nov 2026):
  YouTube   title + description + hashtags are read for search and "related" suggestions. The
            first ~120 characters of the description show under the Short, the first 3 hashtags
            show above the title, and more than 60 hashtags makes YouTube ignore all of them.
            Video tags (snippet.tags, 500 characters max) help with misspellings and search.
  Instagram since Dec 2025 a post may carry at most 5 hashtags; keywords in the caption matter more.
  TikTok    caption keywords drive TikTok search; 3-6 hashtags is the norm.
  Facebook  short caption, a few hashtags.

Works without an LLM (keywords from the source title + transcript and a niche hashtag bank);
with one, the LLM's description, search phrases and hashtags come first.
"""
from __future__ import annotations

import re
from collections import Counter

STOP = set("""a an the and or but if then so to of in on at by for with from as is are was were be been being it its
this that these those i you he she we they me him her us them my your his our their what which who whom whose
when where why how all any both each few more most other some such no nor not only own same than too very can
will just don should now do does did doing have has had having would could there here up down out over under again
further once also into about after before because while vs ft feat official video full part episode ep new best
top live stream streams streaming hd 4k 1080p 60fps pc ps5 xbox gameplay walkthrough commentary no la le de el
yeah oh okay ok like know got get go going gonna wanna um uh hey bro man dude really right well look let see
one two three thing things want need say said think make made let's it's i'm that's what's don't can't
first time played playing ever ki ka ke ne se hai ko mein""".split())

GENERIC_TAGS = ["#shorts", "#viral", "#trending", "#fyp", "#foryou", "#viralvideo", "#shortsfeed", "#explore"]

# niche -> (words that reveal it, hashtags people search in it)
NICHES: dict[str, tuple[set[str], list[str]]] = {
    "gta": ({"gta", "gta5", "gta6", "gtav", "gtavi", "grand theft auto", "rockstar", "los santos", "vice city",
             "gta online", "gtarp"},
            ["#gta", "#grandtheftauto", "#rockstargames", "#gtaonline", "#gtafunny", "#gtamoments"]),
    "gaming": ({"game", "gaming", "gamer", "gameplay", "minecraft", "fortnite", "valorant", "warzone", "cod",
                "roblox", "pubg", "bgmi", "free fire", "apex", "elden ring", "fifa", "eafc", "gta", "playstation",
                "nintendo", "speedrun", "boss", "level", "mission"},
               ["#gaming", "#gamer", "#gamingclips", "#gamingmoments", "#funnygaming", "#gamingcommunity",
                "#videogames"]),
    "streamer": ({"kai cenat", "kaicenat", "ishowspeed", "xqc", "adin ross", "streamer", "twitch", "kick",
                  "livestream", "stream", "marathon", "chat", "subathon", "pokimane", "mrbeast", "sidemen"},
                 ["#streamer", "#twitchclips", "#streamerclips", "#livestream", "#funnymoments", "#reaction"]),
    "anime": ({"anime", "goku", "naruto", "dragon ball", "dbz", "one piece", "luffy", "goten", "vegeta", "whis",
               "ultra instinct", "jujutsu", "demon slayer", "manga", "saiyan"},
              ["#anime", "#animeedit", "#animemoments", "#otaku", "#animefan", "#animeclips"]),
    "comedy": ({"funny", "comedy", "joke", "prank", "lol", "hilarious", "taarak", "tmkoc", "jethalal", "kapil",
                "sitcom", "roast", "meme"},
               ["#comedy", "#funny", "#funnyvideos", "#comedyclips", "#memes", "#lol"]),
    "tv": ({"episode", "serial", "show", "season", "tv", "tmkoc", "taarak mehta", "netflix", "series"},
           ["#tvshow", "#tvclips", "#bingewatch", "#series"]),
    "podcast": ({"podcast", "interview", "joe rogan", "episode", "guest", "host"},
                ["#podcast", "#podcastclips", "#interview", "#podcastmoments"]),
    "sports": ({"football", "soccer", "nba", "basketball", "cricket", "ipl", "goal", "messi", "ronaldo", "ufc",
                "boxing", "wwe", "match"},
               ["#sports", "#highlights", "#football", "#sportsclips"]),
    "motivation": ({"motivation", "success", "mindset", "discipline", "hustle", "money", "business", "grind"},
                   ["#motivation", "#mindset", "#success", "#selfimprovement"]),
}

CTA = {
    "gta": "Follow for daily GTA clips 🎮",
    "gaming": "Follow for daily gaming clips 🎮",
    "streamer": "Follow for the best stream moments every day 🔴",
    "anime": "Follow for daily anime moments ⚡",
    "comedy": "Follow for daily laughs 😂",
    "podcast": "Follow for the best podcast moments 🎙",
    "sports": "Follow for daily highlights 🏆",
    "motivation": "Follow for daily motivation 💪",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s#@'-]", " ", (text or "").lower())).strip()


def detect_niches(*texts: str) -> list[str]:
    blob = " " + _norm(" ".join(t for t in texts if t)) + " "
    squashed = blob.replace(" ", "")
    hits = []
    for name, (words, _tags) in NICHES.items():
        n = sum(1 for w in words if f" {w} " in blob or (len(w) > 4 and w.replace(" ", "") in squashed))
        if n:
            hits.append((n, name))
    return [name for _n, name in sorted(hits, key=lambda x: -x[0])]


def _word_case(w: str) -> str:
    if w.isupper() and len(w) > 4:
        return w.capitalize()  # MARATHON -> Marathon, GTA stays GTA
    return w[:1].upper() + w[1:] if w.islower() else w


def tagify(phrase: str) -> str | None:
    """'Kai Cenat' -> '#KaiCenat'. Keeps letters/digits of any script (Hindi hashtags work too)."""
    parts = re.findall(r"\w+", phrase or "")
    if not parts:
        return None
    t = "".join(_word_case(p) for p in parts)
    return f"#{t}" if 2 <= len(t) <= 30 and not t.isdigit() else None


def _chunks(source_title: str) -> list[str]:
    return [c for c in re.split(r"\s[|•]\s|\s[-–—:]\s|[|•\[\]()]", source_title or "") if c.strip()]


def _entities(source_title: str) -> list[str]:
    """Names and titles from the source title: 'GTA 6 MARATHON | Kai Cenat' -> ['GTA 6', 'Marathon', 'Kai Cenat']."""
    out: list[str] = []
    letters = [c for c in source_title or "" if c.isalpha() and c.isascii()]
    shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.7
    for chunk in _chunks(source_title):
        if shouting:  # ALL-CAPS titles: every word looks like a name, so only keep a short leading phrase
            words = [w.strip("'!?.,") for w in chunk.split() if w.lower().strip("'!?.,") not in STOP]
            if words:
                out.append(" ".join(_word_case(w) for w in words[:2]))
            continue
        for m in re.finditer(r"(?:[A-Z0-9][\w']*(?:\s+|$)){1,3}", chunk):
            words = [w.strip("'") for w in m.group(0).split() if w.lower() not in STOP]
            if not words or all(w.isdigit() for w in words):
                continue
            # 'GTA 6 MARATHON' -> 'GTA 6' + 'Marathon': an all-caps title word after a name/number is its own topic
            if len(words) >= 3 and words[-1].isupper() and len(words[-1]) > 4:
                out += [" ".join(words[:-1]), _word_case(words[-1])]
            else:
                out.append(" ".join(_word_case(w) if w.isupper() and len(w) > 4 else w for w in words))
    return list(dict.fromkeys(e for e in out if len(e) > 1))


def keywords(source_title: str, transcript: str = "", n: int = 12) -> list[str]:
    """Search phrases: names/titles from the source title, combined the way people search, then frequent words."""
    ents = _entities(source_title)
    niches = detect_niches(source_title, transcript)
    phrases: list[str] = []
    if len(_chunks(source_title)) == 2 and len(ents) >= 2:
        phrases.append(f"{ents[-1]} {ents[0]}")  # 'Kai Cenat GTA 6' (person + game) is how people search
    phrases += ents
    lead = ents[0] if ents else None
    if lead:
        phrases += [f"{lead} funny moments", f"{lead} highlights"]
        if "streamer" in niches or "gaming" in niches:
            phrases += [f"{lead} reaction", f"{lead} clips"]
    taken = " ".join(phrases).lower()
    words = [w for w in re.findall(r"[^\W\d_][\w']{2,}", f"{source_title} {transcript}".lower()) if w not in STOP]
    common = [w for w, c in Counter(words).most_common(40) if c >= 2 and w not in taken]
    out: list[str] = []
    for p in phrases + common:
        k = " ".join(p.split())
        if k and k.lower() not in {o.lower() for o in out}:
            out.append(k)
    return out[:n]


def hashtags(*, llm_tags: list[str] | None = None, source_title: str = "", transcript: str = "",
             default_tags: str = "", campaign_tags: str = "", limit: int = 20) -> list[str]:
    """Specific first (names, game), then niche, then broad. Campaign-required tags are always kept."""
    required = [t for t in (campaign_tags or "").split() if t.startswith("#")]
    out: list[str] = []

    def add(t: str | None) -> None:
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t if t.startswith("#") else f"#{t}")

    for t in llm_tags or []:
        add(tagify(t.lstrip("#")) if " " in t else t.replace(" ", ""))
    for k in _entities(source_title)[:4]:
        add(tagify(k))
    niches = detect_niches(source_title, transcript, " ".join(llm_tags or []))
    for name in niches[:3]:
        for t in NICHES[name][1][:5]:
            add(t)
    for t in (default_tags or "").split():
        add(t)
    for t in GENERIC_TAGS:
        add(t)
    room = max(0, limit - len(required))
    chosen = [t for t in out if t.lower() not in {r.lower() for r in required}][:room]
    return list(dict.fromkeys(chosen + required))


def youtube_tags(title: str, source_title: str, kws: list[str], tags: list[str], uploader: str | None) -> list[str]:
    """snippet.tags: total length (with commas/quotes) must stay under 500 characters."""
    cands = [*kws, *(t.lstrip("#") for t in tags if t.lower() not in ("#shorts", "#fyp", "#foryou"))]
    if uploader:
        cands.insert(0, uploader)
    niches = detect_niches(source_title, title, " ".join(kws))
    for name in niches[:2]:
        base = (kws[0] if kws else name)
        cands += [f"{base} funny moments", f"{base} highlights", f"{name} clips"]
    out, total = [], 0
    for c in cands:
        c = " ".join(str(c).replace("#", " ").split())[:60]
        if not c or c.lower() in {o.lower() for o in out}:
            continue
        cost = len(c) + 3
        if total + cost > 480:
            break
        out.append(c)
        total += cost
    return out


def first_line(hook: str | None, title: str, caption: str | None) -> str:
    """The line shown under the video: a curiosity hook, not a repeat of the title."""
    cand = (caption or "").split("\n")[0].strip() or (hook or "").strip() or title
    return cand if cand.endswith(("?", "!", ".", "…", "👀", "😱", "🔥")) else cand + " 👀"


def youtube_description(*, title: str, hook: str | None, caption: str | None, summary: str | None,
                        search_phrases: list[str], tags: list[str], source_title: str = "",
                        uploader: str | None = None, source_url: str | None = None,
                        campaign_extra: str = "", custom: str | None = None) -> str:
    """Detailed description: hook line, what happens, search phrases written as sentences, credit, CTA, hashtags."""
    niches = detect_niches(source_title, title, " ".join(search_phrases))
    cta = CTA.get(niches[0] if niches else "", "Follow for more clips like this every day 🔥")
    parts = [custom.strip()] if custom else [first_line(hook, title, caption)]
    if not custom and summary:
        parts.append(summary.strip())
    elif not custom and caption and caption.strip() != parts[0]:
        parts.append(caption.strip())
    if search_phrases:
        main = ", ".join(search_phrases[:3])
        parts.append(f"In this clip: {main}. " + (f"If you search for {', '.join(search_phrases[3:8])}, "
                                                    "this is the moment everyone is talking about." if len(search_phrases) > 3 else ""))
    parts.append(f"💬 Comment what you would have done!\n👍 Like if this got you\n🔔 {cta}")
    if uploader or source_url:
        parts.append("🎥 Original content: " + " ".join(x for x in (uploader, source_url) if x)
                     + "\nClip shared for commentary and entertainment. All rights to the original creator.")
    if search_phrases:
        parts.append("🔎 " + " | ".join(search_phrases[:15]))
    body = "\n\n".join(p for p in parts if p)
    tag_line = " ".join(tags[:25])
    extra = f"\n{campaign_extra.strip()}" if campaign_extra.strip() else ""
    return f"{body}\n\n{tag_line}{extra}".strip()[:4900]


def short_caption(*, title: str, hook: str | None, caption: str | None, search_phrases: list[str], tags: list[str],
                  max_tags: int, campaign_extra: str = "", custom: str | None = None, limit: int = 2200) -> str:
    """Instagram / TikTok / Facebook: punchy caption, keywords in the text, a few hashtags."""
    lines = [custom.strip()] if custom else [first_line(hook, title, caption)]
    if not custom and caption and caption.strip() != lines[0]:
        lines.append(caption.strip())
    if search_phrases and not custom:
        lines.append(" · ".join(search_phrases[:5]))
    lines.append("Follow for more 🔥")
    tag_line = " ".join(tags[:max_tags])
    text = "\n\n".join(lines) + f"\n\n{tag_line} {campaign_extra}".rstrip()
    return text.strip()[:limit]
