"""Search & discovery packaging: titles, descriptions, hashtags and tags per platform.

What the platforms actually use (checked Nov 2026):
  YouTube   title + description are read for search and "related" suggestions; YouTube's AI also checks
            that they match the video, and keyword-stuffed descriptions are held back. The first
            ~150 characters of the description show in search and under the Short, so they are written
            as ad copy with the main keyword. Only the first 3 hashtags show (above the title), and
            more than 15 makes YouTube ignore all of them, so ClipForge uses 3. "#Shorts" is not needed:
            vertical videos up to 3 min are Shorts. Tags barely matter now except for misspellings,
            so 10-15 clean tags, with common misspellings of the creator's name, go there (never in
            the title).
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
TIKTOK_ONLY = {"#fyp", "#foryou", "#foryoupage", "#explore", "#shortsfeed", "#viralvideo", "#trending", "#viral",
               "#shorts"}

# Spelling: names/titles people search for, so a typo in a title (yours or the AI's) gets corrected.
KNOWN_NAMES = ["Kai Cenat", "IShowSpeed", "xQc", "Adin Ross", "MrBeast", "Pokimane", "Sidemen", "Ninja",
               "Grand Theft Auto", "GTA 6", "GTA 5", "GTA Online", "Rockstar Games", "Vice City", "Los Santos",
               "Minecraft", "Fortnite", "Valorant", "Warzone", "Call of Duty", "Roblox", "Doomsday", "Avengers",
               "Marvel", "Spider-Man", "Dragon Ball", "Goku", "Vegeta", "Naruto", "One Piece", "Taarak Mehta",
               "Jethalal", "Champions League", "Cristiano Ronaldo", "Messi"]
# YouTube categories (https://developers.google.com/youtube/v3/docs/videoCategories)
CATEGORY_BY_NICHE = {"movie": "1", "anime": "1", "gaming": "20", "gta": "20", "streamer": "20", "sports": "17",
                     "comedy": "23", "tv": "24", "podcast": "22", "motivation": "22"}

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
    "movie": ({"trailer", "movie", "film", "marvel", "avengers", "doomsday", "dc", "teaser", "cinema", "box office",
               "spider-man", "spiderman", "batman", "superman", "netflix", "official trailer"},
              ["#movie", "#trailer", "#marvel", "#film", "#movieclips"]),
    "motivation": ({"motivation", "success", "mindset", "discipline", "hustle", "money", "business", "grind"},
                   ["#motivation", "#mindset", "#success", "#selfimprovement"]),
}

CTA = {
    "movie": "Subscribe for the best movie moments 🎬",
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


def _vocab(*texts: str) -> list[str]:
    words: list[str] = []
    for t in list(texts) + KNOWN_NAMES:
        words += re.findall(r"[A-Za-z][A-Za-z'-]{3,}", t or "")
    return list(dict.fromkeys(words))


def _match_case(src: str, fix: str) -> str:
    if src.isupper():
        return fix.upper()
    if src.islower():
        return fix.lower()
    return fix


def fix_spelling(text: str, *reference: str) -> str:
    """Correct names/words that are close to the right spelling in the reference texts (the original video's
    title, the creator's name) or the known-names list: 'KAI Centa' -> 'KAI Cenat', 'doomday' -> 'doomsday'.
    Only words of 4+ letters that aren't in the references are touched, and only when one candidate is clearly
    the closest, so ordinary words are left alone."""
    import difflib

    if not text:
        return text
    vocab = _vocab(*reference)
    lower = {v.lower(): v for v in vocab}
    names = [n for n in KNOWN_NAMES if " " in n] + [e for r in reference for e in _entities(r) if " " in e]

    def fix_word(m: re.Match) -> str:
        w = m.group(0)
        if len(w) < 4 or w.lower() in lower or w.lower() in STOP:
            return w
        cands = difflib.get_close_matches(w.lower(), list(lower), n=2, cutoff=0.8)
        if not cands:
            return w
        if len(cands) == 2 and difflib.SequenceMatcher(None, w.lower(), cands[0]).ratio() == \
                difflib.SequenceMatcher(None, w.lower(), cands[1]).ratio():
            return w  # ambiguous
        return _match_case(w, lower[cands[0]])
    # two-word names first: 'Kai Canta' -> 'Kai Cenat' (first word right, second word close)
    for name in names:
        first, *rest = name.split()
        if not rest:
            continue
        pat = re.compile(rf"\b({re.escape(first)})\s+([A-Za-z]+)\b", re.I)

        def repl(m: re.Match, rest=rest) -> str:
            want = rest[0]
            got = m.group(2)
            if got.lower() == want.lower():
                return m.group(0)
            g, w = got.lower(), want.lower()
            ratio = difflib.SequenceMatcher(None, g, w).ratio()
            overlap = len(set(g) & set(w)) / len(set(g) | set(w))
            close = ratio >= 0.75 or (ratio >= 0.6 and overlap >= 0.8 and len(g) == len(w))
            if g[:1] == w[:1] and len(g) >= 4 and close:
                return f"{m.group(1)} {_match_case(got, want)}"
            return m.group(0)
        text = pat.sub(repl, text)
    return re.sub(r"[A-Za-z][A-Za-z']+", fix_word, text)


def misspellings(name: str, n: int = 2) -> list[str]:
    """Common typos people make when searching a name: swapped letters, a dropped letter."""
    base = (name or "").lower().strip()
    parts = base.split()
    if not parts or len(parts[-1]) < 4:
        return []
    last = parts[-1]
    out = []
    for i in reversed(range(len(last) - 1)):  # adjacent swaps, last letters first ('centa')
        sw = last[:i] + last[i + 1] + last[i] + last[i + 2:]
        if sw != last:
            out.append(" ".join(parts[:-1] + [sw]))
    out.insert(0, " ".join(parts[:-1] + [last[:-1] + "nt" if last.endswith("t") else last + "n"]))
    return list(dict.fromkeys(o for o in out if o != base))[:n]


def category_id(*texts: str, default: str = "20") -> str:
    niches = detect_niches(*texts)
    for n in niches:
        if n in CATEGORY_BY_NICHE:
            return CATEGORY_BY_NICHE[n]
    return default


def hashtags(*, llm_tags: list[str] | None = None, source_title: str = "", transcript: str = "",
             default_tags: str = "", campaign_tags: str = "", limit: int = 3, platform: str = "youtube") -> list[str]:
    """Most specific first (names, game/show), then niche. Campaign-required tags are always kept.
    YouTube and Instagram get no TikTok-style filler (#fyp, #foryou, #viral...)."""
    required = [t for t in (campaign_tags or "").split() if t.startswith("#")]
    out: list[str] = []
    filler_ok = platform == "tiktok"

    def add(t: str | None) -> None:
        if not t:
            return
        t = t if t.startswith("#") else f"#{t}"
        t = "#" + re.sub(r"[^\w]", "", t[1:])
        if len(t) < 3 or t.lower() in {o.lower() for o in out}:
            return
        if not filler_ok and t.lower() in TIKTOK_ONLY:
            return
        out.append(t)

    for t in llm_tags or []:
        add(tagify(t.lstrip("#")) if " " in t else t.replace(" ", ""))
    known = {k.lower() for k in KNOWN_NAMES}
    ents = sorted(_entities(source_title), key=lambda e: (e.lower() not in known, " " not in e))  # names first
    for k in ents[:3]:
        add(tagify(k))
    niches = detect_niches(source_title, transcript, " ".join(llm_tags or []))
    for name in niches[:2]:
        for t in NICHES[name][1][:3]:
            add(t)
    for t in (default_tags or "").split():
        add(t)
    if filler_ok:
        for t in GENERIC_TAGS:
            add(t)
    room = max(0, limit - len(required))
    chosen = [t for t in out if t.lower() not in {r.lower() for r in required}][:room]
    return list(dict.fromkeys(chosen + required))


def _clean_tag(t: str) -> str | None:
    words = re.sub(r"[#_]", " ", str(t)).lower().split()
    seen: list[str] = []
    for w in words:  # 'gta 6 funny moments funny moments' -> 'gta 6 funny moments'
        if w not in seen or w.isdigit():
            seen.append(w)
    c = " ".join(seen)[:60].strip()
    return c if len(c) >= 2 else None


def youtube_tags(title: str, source_title: str, kws: list[str], tags: list[str], uploader: str | None,
                 n: int = 15) -> list[str]:
    """10-15 clean, de-duplicated tags: main keyword, its variants, the creator's name and 2 common misspellings
    of it (misspellings belong here, never in the title). Under YouTube's 500-character limit."""
    creator = _creator(uploader, source_title)
    cands: list[str] = [*kws[:8]]
    if creator:
        cands.append(creator)
    # '#GTA6FunnyMoments' -> 'gta 6 funny moments' (squashed hashtags aren't how people search)
    known = {(tagify(k) or "").lower(): k.lower() for k in KNOWN_NAMES}
    cands += [known.get(t.lower()) or
              re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])|(?<=[A-Z])(?=[0-9])|(?<=[A-Z])(?=[A-Z][a-z])",
                     " ", t.lstrip("#"))
              for t in tags if t.lower() not in TIKTOK_ONLY]
    ents = _entities(source_title)
    if ents:
        cands += [f"{ents[0]} funny moments", f"{ents[0]} highlights"]
    if creator:
        cands += misspellings(creator)
    out, total = [], 0
    for c in cands:
        c = _clean_tag(c)
        if not c or c in out:
            continue
        cost = len(c) + 3
        if total + cost > 480:
            break
        out.append(c)
        total += cost
        if len(out) >= n:
            break
    return out


def _creator(uploader: str | None, source_title: str) -> str | None:
    """'Kai Cenat Live' -> 'Kai Cenat'."""
    if uploader:
        u = re.sub(r"\b(live|official|clips|tv|vods?|channel|gaming)\b", "", uploader, flags=re.I)
        u = " ".join(u.split())
        return u or uploader
    for name in KNOWN_NAMES:
        if name.lower() in (source_title or "").lower() and " " in name:
            return name
    return None


def primary_keyword(source_title: str, uploader: str | None, phrases: list[str]) -> str:
    """'kai cenat gta 6': creator + game/show, the way people search."""
    if phrases:
        return phrases[0]
    ents = _entities(source_title)
    creator = _creator(uploader, source_title)
    topic = next((e for e in ents if not creator or e.lower() not in creator.lower()), None)
    return " ".join(x for x in (creator, topic) if x).lower() or (ents[0].lower() if ents else "")


def proper_case(phrase: str, reference: str = "") -> str:
    """'kai cenat gta 6' -> 'Kai Cenat GTA 6': each word as the reference/known names write it, else Title."""
    forms = {w.lower(): w for w in re.findall(r"[\w']+", " ".join([reference or "", *KNOWN_NAMES]))
             if not w.islower() and not (w.isupper() and len(w) > 4)}
    return " ".join(forms.get(w.lower(), w.capitalize() if w.islower() else w) for w in (phrase or "").split())


def ad_copy(summary: str | None, hook: str | None, title: str, keyword: str, limit: int = 150,
            reference: str = "", caption: str | None = None) -> str:
    """First line of the description: what happens, with the main keyword, in at most ~150 characters."""
    first = re.split(r"(?<=[.!?])\s", (summary or "").strip())[0] if summary else ""
    cap = (caption or "").strip()
    line = first or (cap if cap and not cap.endswith("?") else "") or (title or hook or "").strip()
    have = set(re.findall(r"\w+", line.lower()))
    if keyword and not set(re.findall(r"\w+", keyword.lower())) <= have:
        line = f"{proper_case(keyword, reference)}: {line}"
    if len(line) > limit:
        line = line[:limit - 1].rsplit(" ", 1)[0] + "…"
    return line


def youtube_description(*, title: str, hook: str | None, caption: str | None, summary: str | None,
                        search_phrases: list[str], tags: list[str], source_title: str = "",
                        uploader: str | None = None, source_url: str | None = None,
                        campaign_extra: str = "", custom: str | None = None) -> str:
    """Ad-copy first line, what happens (natural sentences), credit, a question to comment on, 3 hashtags.
    No keyword lists: YouTube treats them as stuffing."""
    niches = detect_niches(source_title, title, " ".join(search_phrases))
    cta = CTA.get(niches[0] if niches else "", "Subscribe for more clips like this 🔥")
    keyword = primary_keyword(source_title, uploader, search_phrases)
    if custom:
        parts = [custom.strip()]
    else:
        parts = [ad_copy(summary, hook, title, keyword, reference=source_title, caption=caption)]
        rest = (summary or "").strip()
        rest = rest[len(re.split(r"(?<=[.!?])\s", rest)[0]):].strip() if rest else ""
        if rest:
            parts.append(rest)
    question = (caption or "").strip() if caption and caption.strip().endswith("?") else \
        "What would you have done? Tell us in the comments 👇"
    parts.append(f"{question}\n🔔 {cta}")
    if uploader or source_url:
        parts.append("🎥 Original: " + " · ".join(x for x in (uploader, source_url) if x)
                     + "\nAll rights belong to the original creator.")
    body = "\n\n".join(p for p in parts if p)
    tag_line = " ".join(tags[:3])
    extra = f"\n{campaign_extra.strip()}" if campaign_extra.strip() else ""
    return f"{body}\n\n{tag_line}{extra}".strip()[:4900]


def short_caption(*, title: str, hook: str | None, caption: str | None, search_phrases: list[str], tags: list[str],
                  max_tags: int, campaign_extra: str = "", custom: str | None = None, limit: int = 2200,
                  summary: str | None = None, keyword: str = "", reference: str = "") -> str:
    """Instagram / TikTok / Facebook: one ad-copy line with the keyword, a question, a few hashtags."""
    lines = [custom.strip()] if custom else [ad_copy(summary, hook, title, keyword, reference=reference, caption=caption)]
    if not custom and caption and caption.strip() != lines[0]:
        lines.append(caption.strip())
    tag_line = " ".join(tags[:max_tags])
    text = "\n\n".join(lines) + f"\n\n{tag_line} {campaign_extra}".rstrip()
    return text.strip()[:limit]
