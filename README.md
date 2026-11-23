<h1 align="center">ClipForge</h1>
<p align="center"><b>Turn any video into viral short clips, on your own computer, for free.</b></p>

<p align="center"><img src="docs/screenshots/clips.jpg" alt="ClipForge clips page" width="900"></p>

Paste a video link, upload a file or follow a live stream. ClipForge finds the most exciting moments, cuts them into Shorts / Reels / TikToks with word-by-word captions, a hook title, a 2-second tease of the payoff, jump cuts, zoom punch-ins, a slow-motion replay that loops back to the start, a ducked background beat, a progress bar and a cover image, and scores how viral each one looks. Every post gets a clean, search-friendly description (an ad-copy first line with the main keyword, no keyword stuffing), 3 specific hashtags, 10-15 tags, the right YouTube category and correctly spelled names. You review them in the web app or in Telegram, and approved clips are posted to YouTube, Instagram, Facebook and TikTok (all of them, or just the ones you pick), now or on a schedule.

Everything runs on your computer: speech-to-text with Whisper, and the AI writer with Ollama or any cloud model you choose. No subscriptions.

> Screenshots in this README use generated demo footage, not real content.

## Contents

1. [Features](#features)
2. [Install](#1-install)
3. [Start and stop](#2-start-and-stop)
4. [Using the app](#3-using-the-app)
5. [Telegram bot: set up and link to the app](#4-telegram-bot-set-up-and-link-to-the-app)
6. [Using the bot](#5-using-the-bot)
7. [AI writer (optional)](#6-ai-writer-optional)
8. [Connect YouTube, Instagram, Facebook and TikTok](#7-connect-youtube-instagram-facebook-and-tiktok)
9. [Earn from clipping](#8-earn-from-clipping)
10. [Commands](#9-commands)
11. [Settings (.env)](#10-settings-env)
12. [Getting views](#getting-views) · [SEO rules](#seo-rules) · [Updating](#updating) · [Troubleshooting](#troubleshooting) · [Server / Docker](#run-on-a-server-docker) · [Tests](#tests)

---

## Features

| | |
|---|---|
| **Sources** | Video links (YouTube, Twitch, Kick, TikTok, X, Instagram, Reddit, direct .mp4/.m3u8), uploaded files, and live streams (clips are cut while the stream is still running) |
| **Shapes** | Vertical 9:16, portrait 4:5, square 1:1 and landscape 16:9, in any mix per job |
| **Finding moments** | Audio excitement plus on-screen motion, optionally re-ranked by the AI from what is said |
| **Viral look** | Pop captions (the spoken word in yellow, key words in green), a hook title for the first 3 s, jump cuts (only real dead air, never silent action), zoom punch-ins, a progress bar and your watermark |
| **Viral edit** | Opens with a 2 s tease of the payoff moment ("wait for it"), punches in on the payoff, and ends on a zoomed slow-motion **INSTANT REPLAY** that loops straight back into the opening. Optional background beat (your own royalty-free tracks, or a built-in one ClipForge generates), ducked whenever someone talks |
| **SEO & hashtags** | Follows YouTube's own rules (see [SEO rules](#seo-rules)): a first line under 150 characters that reads like an ad and holds the main keyword, what happens in plain sentences, a question, credit to the original creator, and **3 hashtags** (no #fyp or #shorts). 10-15 clean YouTube tags with no repeated words, including 2 common misspellings of the creator's name (misspellings only ever go in tags). Names are spell-checked against the original video ("Kai Centa" becomes "Kai Cenat"), the category matches the content (trailers go to Film & Animation, games to Gaming), and titles carry no "#Shorts". Instagram gets 5 hashtags (its limit), TikTok 5, Facebook 3. **📝 Post text** shows exactly what each platform gets |
| **No duplicate spam** | Every clip gets its own title. A clip from the same video approved minutes after another is scheduled for the next free slot (90 min apart by default); you can publish it anyway, or **merge** the clips into one compilation |
| **Live progress** | Every job shows its steps as they happen: download % and speed, finding moments, transcribing, AI writing, payoff/cut/zoom plan, cover, render %, and for publishing: upload %, metadata, thumbnail, **visibility check** and the live link |
| **Smart crop** | Follows faces (streamer cam, podcast guests) and action instead of always cropping the centre |
| **Cover image** | Made for every clip from the sharpest frame around the payoff (the reaction), with the hook in big bold text, and used as its first frame, so YouTube, Instagram and TikTok all show it as the thumbnail |
| **AI writer** | Hook-first title with the searched keyword up front, hook, caption, description, search phrases, hashtags, the payoff second and a virality score, plus alternative hooks and titles to choose from. Without AI, ClipForge builds the same package from the source title and transcript |
| **Edit before posting** | Change the title, hook, caption or cover in the app or with buttons in Telegram |
| **Copyright check** | Gives each link, live stream and upload a risk level, says whether the original is likely monetised, and suggests what to do |
| **Publishing** | YouTube (Shorts or normal videos), Instagram Reels, Facebook Reels and TikTok, posted now or on a schedule. Choose per clip which platforms get it, in the app or in Telegram |
| **Earn** | Tracks paid clipping campaigns (Whop Content Rewards, Vyro, ClipAffiliates, Promote.fun, direct deals). Adds the required hashtags, keeps post links ready to submit, and estimates earnings |

<p align="center"><img src="docs/screenshots/clip-frames.jpg" alt="A finished clip: cover as first frame, hook title, pop captions, watermark and progress bar" width="700"><br>
<sub>A finished vertical clip: the cover as the first frame, then the hook, pop captions, @watermark and progress bar.</sub></p>

---

## 1. Install

### Mac

You need a Mac (Apple Silicon or Intel), about 5 GB of free space, and [Homebrew](https://brew.sh).

```bash
# 1. Tools. ffmpeg-full includes libass, which captions need.
brew install ffmpeg-full python@3.12 git deno   # deno lets yt-dlp see all YouTube formats
brew link --force ffmpeg-full
ffmpeg -filters | grep " ass "        # must print a line, otherwise captions are skipped

# 2. Get ClipForge
git clone https://github.com/amitkumarsingh1697/ClipForge.git
cd ClipForge

# 3. Python environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Your settings file
cp .env.example .env
```

### Windows / Linux

Install Python 3.12, git and ffmpeg built with libass (most Linux packages have it; on Windows use a "full" build from gyan.dev and add it to PATH). Then run steps 2 to 4 above. On Windows, activate the environment with `.venv\Scripts\activate`.

---

## 2. Start and stop

```bash
cd ClipForge
source .venv/bin/activate
python -m app.cli serve
```

Your browser opens **http://127.0.0.1:8000**. The first clip downloads the Whisper speech model (~500 MB), so it takes a few minutes; later clips are faster.

**Keep it running in the background** (it keeps running after you close Terminal):

```bash
nohup python -m app.cli serve > data/serve.log 2>&1 &
```

**Stop it:**

```bash
pkill -f "app.cli serve"
```

**See what it's doing:** `tail -f data/serve.log`

---

## 3. Using the app

The sidebar has five pages: **Create**, **Clips**, **Earn**, **Publishing** and **AI & brand**. The dot next to each name at the top turns green when that service is connected.

### Create clips

<p align="center"><img src="docs/screenshots/create.jpg" alt="Create page" width="900"></p>

1. **Source:** paste a link, or switch to **Upload** (a file from your computer) or **Live stream** (a Twitch, Kick or YouTube live channel).
   Press **🛡 Check copyright** to see who owns the video and how risky it is to repost.
2. **Shape:** pick one shape: Vertical 9:16 (Shorts, Reels, TikTok), Portrait 4:5, Square 1:1 or Landscape 16:9.
3. **Clips per video** and **Clip length** (up to 5 min; Shorts count only up to 3 min). **Title** is optional.
4. **Style:** caption style, an optional **Campaign** (see [Earn](#8-earn-from-clipping)), and the extras:
   - **Hook title:** big curiosity text for the first 3 seconds
   - **Jump cuts:** removes pauses in talking (never silent action scenes, at most 25% of a clip)
   - **Zoom punch-ins:** quick zooms on key words
   - **Progress bar** along the bottom
   - **Cover as first frame:** makes the cover the thumbnail everywhere
   - **Payoff tease:** the clip opens with 2 seconds of its best moment, under the hook, then plays from the start
   - **Slow-mo replay:** ends on a zoomed, slowed-down replay of the payoff; because the clip opens on the same moment, it loops smoothly (replays count as views)
   - **Background beat:** a quiet beat that ducks under speech. Put your own royalty-free/CC0 tracks (`.mp3 .wav .m4a`) in `data/music/` and ClipForge rotates through them; with none it uses a beat it generates itself. Never use commercial songs: Content ID mutes or blocks them
5. Press **✦ Make clips** (or **● Start recording** for a live stream; press **■ Stop recording** on the job to end it).

### Watch the progress

Every video and every clip shows its steps live, so you always know what it's doing:

<p align="center"><img src="docs/screenshots/progress.jpg" alt="Live steps while a clip renders and publishes" width="700"></p>

- **Video:** Download (percent, MB, speed, time left) → Find exciting moments → AI picks the best moments → Queue clips
- **Clip:** Transcribe speech → Write title, hook, description & hashtags → Find the payoff, plan cuts, zooms & replay → Make thumbnail cover → Render (percent) → Send for review
- **After Approve:** per platform, e.g. YouTube: Upload (percent) → Title, description, hashtags & tags → Thumbnail → Check visibility → Live link

A finished list folds into one line ("✅ Clip made · 6/6 done"); click it to open the details. A step that needed a fallback shows ⚠️ with the reason (for example "AI failed, using automatic SEO"), and a failed step shows ❌ with the error.

### Review, edit and approve

On **Clips**, each video you submitted is a group with its copyright badge, and each clip card shows the cover, the 🔥 virality score, the hook, hashtags and details (length, cuts, zooms, campaign). Use the filter at the top to show only *Ready for review*, *Scheduled*, *Published* or *Failed*.

| Button | What it does |
|---|---|
| **✓ Approve** | Opens the publish dialog. Untick any platform you don't want under **Post to** (all connected ones are ticked; ones not connected yet show 🔒), then **Publish now**, or pick a time (or a quick choice such as *Tonight 7 PM*) and **Schedule**. If a clip from the same video went out minutes ago, the dialog warns you and offers **Schedule for the next slot**, **Merge both into a compilation** or **Publish now anyway** (Telegram schedules it and sends a *Publish now anyway* button) |
| **Reject** | Deletes the clip |
| **✎ Edit** | Change the title, hook, caption or cover (see below) |
| **⬇ Video / ⬇ Cover** | Download the MP4 or the cover JPG |
| **📝 Post text** | The exact YouTube title, description and tags, and the Instagram, TikTok and Facebook captions, with Copy buttons |
| **🌍 Make public on YouTube** | Shown when a published clip is private or unlisted on YouTube. The Clips page also offers **Make them all public** and **Check visibility** for uploads made before this check existed |
| **🎞 Merge N clips into a compilation** | On a video with 2+ clips waiting for review: joins them into one longer video, a new clip you approve like any other. Better than posting near-identical Shorts minutes apart |
| **↻ Remake** | Make the clip again with a fresh AI hook, title and caption |
| **Retry** | On a failed clip: render again, or publish again to the platforms that failed |

<p align="center"><img src="docs/screenshots/edit.jpg" alt="Edit dialog" width="440"> <img src="docs/screenshots/schedule.jpg" alt="Schedule dialog" width="440"></p>

**Edit:** tap one of the AI's alternative titles or hooks, or type your own. For the cover, write different text, slide to the moment you want, upload your own image, or go back to the automatic cover. A changed title or caption is saved straight away; a changed hook or cover re-makes the clip in about a minute.

If no account is connected, **Approve** just marks the clip approved, and you download it and post it yourself.

The app works on a phone too (open it on your network, or use Telegram):

<p align="center"><img src="docs/screenshots/mobile.jpg" alt="ClipForge on a phone" width="260"></p>

---

## 4. Telegram bot: set up and link to the app

The bot sends you every finished clip with buttons to approve, reject or edit it, and you can send it links to clip from your phone. It talks to the ClipForge running on your computer and needs no public address, so it works on home Wi-Fi.

**Step 1: create the bot**

1. In Telegram, search for **@BotFather** (the official one, with a blue tick) and open it.
2. Send `/newbot`.
3. Send a display name, e.g. `My Clips`.
4. Send a username ending in `bot`, e.g. `myclips1234_bot`.
5. BotFather replies with a **token** like `1234567890:AAH...`. Copy it and keep it private: anyone with it controls the bot.

**Step 2: put the token in ClipForge**

Open the `.env` file in the ClipForge folder (e.g. `open -e .env` on a Mac) and set:

```
TELEGRAM_BOT_TOKEN=1234567890:AAH...your token...
```

**Step 3: link the bot to you**

Stop ClipForge first if it's running (otherwise it would read your message before the setup does):

```bash
pkill -f "app.cli serve"
python -m app.cli telegram-setup
```

It prints `Bot OK: @yourbot. Open https://t.me/yourbot and send it /start ...`. Open that link, press **Start** (or send `/start`) within 5 minutes. The bot answers *✅ Connected*, and your chat id is saved to `.env` as `TELEGRAM_OWNER_CHAT_ID`. From now on the bot only listens to you; messages from anyone else are ignored.

**Step 4: start ClipForge again**

```bash
nohup python -m app.cli serve > data/serve.log 2>&1 &
```

The log shows `Telegram bot is listening`, and the **Telegram** dot in the app turns green.

**Change or reset the bot:** in @BotFather send `/revoke` to get a new token (do this if the token was ever shared), put it in `.env` and restart. To link a different Telegram account, run `telegram-setup` again from that account.

---

## 5. Using the bot

```
You:  https://youtu.be/abc123 landscape square
Bot:  Queued https://youtu.be/abc123 (landscape, square) as job 3f9c1a2b7d10.
      I'll send clips here for approval.

Bot:  [video] [cover image]
      🔥 Virality 88/100
      🪝 He did NOT see this coming
      The Craziest Move Ever 😱
      Would you have tried this? 👇
      #gaming #clutch #viral
      32s vertical clip
      🟡 Copyright risk: medium (original is probably monetised)
      [✅ Approve & Publish] [❌ Reject & Delete]
      [✏️ Title] [🪝 Hook] [🖼 Cover]
      [☑️ YouTube] [☑️ Instagram]
      [☑️ Facebook] [🔒 TikTok]

You:  (tap ☑️ Facebook)
Bot:  Will post to: YouTube, Instagram      ← the button turns into ⬜ Facebook
You:  (tap ✅ Approve & Publish)
Bot:  ✅ Published:
      YouTube: https://youtube.com/shorts/...
      Instagram: https://www.instagram.com/reel/...
```

| You send / tap | What happens |
|---|---|
| A video or live link | Clips are made. Add shape words to choose shapes: `vertical`, `portrait`, `square`, `landscape` (default: the app's default) |
| `/check https://...` | Copyright and monetisation check before you clip |
| `/help` | Shows these commands |
| **☑️ / ⬜ platform buttons** | Choose where this clip goes. Every connected platform starts ticked; tap one to untick it (or tick it again). At least one stays ticked. 🔒 means that platform isn't connected yet (connect it on **Publishing**) |
| **✅ Approve & Publish** | Posts to the ticked platforms (or marks it approved if none are connected) |
| **❌ Reject & Delete** | Deletes the clip |
| **✏️ Title** | Shows the AI's alternative titles: tap one, or type your own |
| **🪝 Hook** | Same for the hook; send `none` to remove it. The clip is re-made and sent again |
| **🖼 Cover** | Send a **photo** to use as the cover, a **number** (e.g. `12`) to use that second of the clip, or **text** to write on it. Or tap *Back to automatic cover* |

You also get messages when a clip is published (with links, and a reminder to submit them to your campaign), when something fails, and when a source looks high-risk for copyright.

Clips over ~48 MB are sent as a smaller preview, because Telegram bots can send at most 50 MB; the full-quality file is what gets published.

---

## 6. AI writer (optional)

Clips work without any AI. With one, ClipForge writes the hook, title (plus alternatives), caption, hashtags and highlighted key words from what is said, and can help pick the best moments.

<p align="center"><img src="docs/screenshots/ai.jpg" alt="AI and brand settings" width="900"></p>

**Local and free (recommended):**

```bash
brew install ollama && brew services start ollama
ollama pull llama3.1:8b          # ~5 GB; on 8 GB Macs use llama3.2:3b
```

Then go to **AI & brand**, set Provider to **Ollama (local)**, pick the model, and press **Save**, then **Test**. LM Studio, llama.cpp, Jan, vLLM and LocalAI work too.

**Cloud models** need an API key from the provider's developer site. The ChatGPT, Claude and Gemini chat apps can't be connected directly.

| Provider | Get a key | Notes |
|---|---|---|
| Claude (Anthropic) | console.anthropic.com | |
| ChatGPT (OpenAI) | platform.openai.com/api-keys | |
| Gemini (Google) | aistudio.google.com/apikey | has a free tier |
| Kimi (Moonshot) | platform.moonshot.ai | |
| DeepSeek, Groq, OpenRouter, Mistral | their developer consoles | Groq and OpenRouter have free options |

Each provider keeps its own key, so you can switch back and forth. Under **Your brand**, set a **watermark** (your @handle on every clip) and **default hashtags**. **Speech-to-text model:** `small` is a good default; `medium` or `large-v3` are more accurate but slower.

---

## 7. Connect YouTube, Instagram, Facebook and TikTok

<p align="center"><img src="docs/screenshots/publish.jpg" alt="Publishing page" width="900"></p>

### YouTube

1. In [Google Cloud Console](https://console.cloud.google.com/projectcreate), create a project (it's free).
2. Enable **[YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)**.
3. Open **Google Auth Platform** (the OAuth consent screen). Choose External, enter an app name and your email, add your Google account as a test user, then press **Publish app** (In production). In Testing mode, sign-in expires every 7 days.
4. Go to **Credentials → Create credentials → OAuth client ID → Desktop app** and download the JSON file.
5. In ClipForge, open **Publishing**, upload the JSON, press **Connect YouTube**, sign in and allow access. If Google says the app is unverified, choose Advanced → continue; it's your own app.

Uploads are **public** by default (`YOUTUBE_PRIVACY=public`). A private or unlisted video gets no views, because nobody can find it.

After every upload ClipForge asks YouTube who can actually see the video and shows it in the **Check visibility** step. Google locks uploads from Cloud projects that haven't passed its [API audit](https://support.google.com/youtube/answer/7300965) to private. If that happens, the step says so: fill in the free [audit form](https://support.google.com/youtube/contact/yt_api_form) and, until it's approved, download those clips (**⬇ Video**, **📝 Post text** → Copy) and upload them in the YouTube app.

Clips up to 3 min that aren't landscape are posted as Shorts. The free quota allows about 6 uploads a day (one upload costs ~1,600 of 10,000 units). Custom thumbnails need a phone-verified channel ([youtube.com/verify](https://www.youtube.com/verify)); the cover is also the first frame, so Shorts show it either way.

### Instagram Reels

1. Switch your Instagram to a **Professional** (Business or Creator) account and link it to a Facebook Page.
2. Create an app at [Meta for Developers](https://developers.facebook.com/apps) and add the Instagram product.
3. Generate a long-lived access token with `instagram_basic`, `instagram_content_publish` and `pages_show_list`. Copy your Instagram account ID too.
4. Paste the ID and token, and optionally the app ID and secret so the token refreshes automatically. Press **Save & check**.

### Facebook Reels

Facebook pays Pages for Reels views through its content monetization programme, so it's worth posting there too.

1. You need a Facebook **Page** (personal profiles can't be posted to by apps). The Page linked to your Instagram works.
2. In the same Meta app as Instagram, open [Graph API Explorer](https://developers.facebook.com/tools/explorer/) and get a User token with `pages_show_list`, `pages_read_engagement` and `pages_manage_posts`.
3. Exchange it for a long-lived token ([Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/) → *Extend*), then call `me/accounts` in the Explorer. Copy your Page's **id** and **access_token**. A Page token made this way doesn't expire.
4. Paste them in **Publishing → Facebook Reels** and press **Save & check**.

Facebook Reels must be 3 to 90 seconds; longer clips skip Facebook and still go to the other platforms. Meta allows 30 reels per Page a day through the API.

### TikTok

1. Create an app at [TikTok for Developers](https://developers.tiktok.com/apps) with **Login Kit** and **Content Posting API** (Direct Post), using the scope `video.publish`.
2. Sign in to your TikTok account through that app to get an access token and a refresh token.
3. Paste the client key, client secret, access token and refresh token, then press **Save & check**. Tokens refresh automatically.

Until TikTok audits your app, posts are **private** (only you can see them).

---

## 8. Earn from clipping

<p align="center"><img src="docs/screenshots/earn.jpg" alt="Earn page" width="900"></p>

Creators and brands pay clippers per 1,000 views.

1. On **Earn**, open a marketplace and join a campaign there. Built in: Whop Content Rewards, Content Rewards, Vyro, ClipAffiliates, Promote.fun, Clipster, Clipping.net, Kick Bounties, or a direct deal with a creator.
2. In **Add a campaign**, enter its name, pay per 1K views, page link, and any required hashtags and @mention.
3. On **Create**, pick that campaign. Its hashtags and mention are added to every caption.
4. After you publish, the post links appear under the campaign with **Copy** buttons. Submit them on the marketplace. None of them offer an API, so this step is yours.
5. **↻ Refresh views** reads YouTube and Instagram view counts and estimates what you've earned.

**Where the money comes from**

| Source | How you get paid | Connected in ClipForge |
|---|---|---|
| YouTube Partner Program (Shorts) | Ad revenue share once the channel qualifies | Auto-post |
| Facebook content monetization (Reels) | Paid per view on your Page | Auto-post |
| TikTok Creator Rewards | Paid for videos over 1 minute, in supported countries | Auto-post |
| Instagram | No general per-view pay in most countries; good for growth and brand deals | Auto-post |
| Clipping marketplaces (Whop, Vyro, Clipster, ...) | Per 1K views on campaigns you join | Campaign tracking, hashtags, links to submit |
| Snapchat Spotlight, X (Twitter) creator payouts | Per view / ad share | Not automatic: Snapchat has no posting API, and X charges for its API. Download the clip and post it yourself |

**About copyright:** re-uploading other people's videos (TV, anime, music, sports, other creators) can get Content ID claims or strikes. A channel made only of re-uploads can also be refused monetisation as "reused content". Use the copyright check, clip creators who allow or pay for clips, add your own commentary, and credit the original. The check is an estimate based on public information, not legal advice.

---

## 9. Commands

Run these in the ClipForge folder with the environment active (`source .venv/bin/activate`).

| Command | What it does |
|---|---|
| `python -m app.cli serve` | Starts everything: the web app, background workers, the Telegram bot and the scheduler. Options: `--host 0.0.0.0` (reachable from other devices on your network; set `API_TOKEN` first) and `--port 8000` |
| `python -m app.cli telegram-setup` | Checks the bot token and links your Telegram account (stop `serve` first) |
| `python -m app.cli clip SOURCE` | Clips a link or a local file without the web app. Options: `--formats vertical,landscape`, `--clips 3`, `--seconds 45`, `--title "..."` |
| `python -m app.cli live URL --minutes 30` | Records a live stream for N minutes and clips highlights. Option: `--formats` |
| `python -m app.cli poll` | Runs only the Telegram bot and the scheduler (used by Docker) |
| `pkill -f "app.cli serve"` | Stops the app |
| `tail -f data/serve.log` | Shows the live log |
| `pytest -q` | Runs the tests |

---

## 10. Settings (.env)

Most things are set in the app. `.env` holds secrets and defaults; restart after editing it. Commonly changed values:

| Setting | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_CHAT_ID` | empty | Telegram bot (see section 4) |
| `API_TOKEN` | empty | Leave empty on your own computer (only this computer can open the app). Set a long random string to allow other devices |
| `CLIP_MAX_S` / `CLIP_TARGET_S` / `CLIPS_PER_SOURCE` | 300 / 30 / 3 | Longest clip, default length, clips per video |
| `OUTPUT_FORMATS` | vertical | Default shape in the app: vertical, portrait, square or landscape (Telegram links can still name several) |
| `CAPTION_STYLE` | pop | pop, classic, minimal or none |
| `HOOK_OVERLAY`, `JUMP_CUTS`, `ZOOM_PUNCH`, `PROGRESS_BAR`, `COVER_INTRO` | true | Default extras |
| `PAYOFF_TEASE`, `PAYOFF_REPLAY`, `BEAT` | true | Payoff tease at the start, slow-mo replay at the end, background beat |
| `BEAT_VOLUME` | 0.12 | Beat loudness before ducking (0 to 1) |
| `WHISPER_MODEL` | small | tiny, base, small, medium, large-v3 |
| `YOUTUBE_CATEGORY_ID` | 20 | Only a fallback: each clip gets the category that matches it (1 Film & Animation, 17 Sports, 20 Gaming, 22 People & Blogs, 23 Comedy, 24 Entertainment) |
| `PUBLISH_SPACING_MIN` | 90 | Clips from the same video are posted at least this many minutes apart; 0 turns it off |
| `YOUTUBE_PRIVACY` | public | public, unlisted or private. Older `.env` files said `private`: change it to `public` |
| `TIKTOK_PRIVACY` | SELF_ONLY | Unaudited TikTok apps can only post privately |
| `DATA_DIR` | ./data | Where videos, the database and tokens are kept |

---

## Updating

```bash
cd ClipForge
git pull
source .venv/bin/activate && pip install -r requirements.txt
pkill -f "app.cli serve"; nohup python -m app.cli serve > data/serve.log 2>&1 &
```

Then refresh the browser with Cmd+Shift+R.

## Troubleshooting

| Problem | Fix |
|---|---|
| Live stream: "Requested format is not available" / "list of available formats" | Fixed in the latest ClipForge: YouTube often sends live streams as separate video and audio tracks, which ClipForge now records together (vertical streams in 1080p). Update ClipForge, then `pip install -U yt-dlp` and `brew install deno` |
| Live stream: "hasn't started yet" or "isn't live any more" | Wait until it is live, or paste a finished stream under **Link** to clip the recording |
| Captions missing, "ffmpeg has no libass" | `brew install ffmpeg-full && brew link --force ffmpeg-full`, then restart |
| Captions empty (0 words) | Run `pip install -r requirements.txt` again (PyAV must be below version 19) |
| `SSL: UNEXPECTED_EOF`, or downloads from YouTube/Twitch/Instagram fail | Your network (office or managed Wi-Fi) blocks those sites. Use a hotspot or VPN, or download the video and use **Upload** |
| Clip length slider stops at 60 s | An old `.env` has `CLIP_MAX_S=60`. Change it to `300` and restart |
| "Connect YouTube, Instagram or TikTok" after Approve | No account is connected yet; see section 7 |
| "Skipped: Facebook (Reels must be 90 s or shorter)" | Facebook only takes reels up to 90 s. Make shorter clips, or untick Facebook for long ones |
| Clips get **zero views** | Open **Clips**: if the yellow box says uploads aren't public, press **Make them all public**. Check the **Check visibility** step of new uploads (Google may lock them to private until your project is audited, see section 7). Also see [Getting views](#getting-views) |
| YouTube views don't load | Press **Connect YouTube** again (reading views needs a newer permission) |
| `telegram-setup` waits forever | Stop `serve` first, then press **Start** in the bot within 5 minutes |
| The bot doesn't answer | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` in `.env`, restart, and look for `Telegram bot is listening` in the log. The bot answers only its owner |
| Telegram says a video is too large | Clips over ~48 MB are sent as a smaller preview; the full file stays in the app |
| AI test fails (Ollama) | Run `brew services start ollama`; `ollama list` should show the model |
| AI test fails (cloud) | Check the API key, and that your account has credit or free-tier access |
| A clip seems stuck rendering | Each render has a time limit and then fails with a message; press **Retry** |
| Port 8000 in use | Run `pkill -f "app.cli serve"` and start again, or use `--port 8001` |
| Anything else | Check `data/serve.log`. Tokens are masked in it, but don't share it publicly |

## Getting views

ClipForge does the packaging the big clip channels do: a hook in the first second, a payoff tease, captions, zooms, a replay that loops, a clear cover, and a description full of the words people search for. That raises the odds, but it doesn't guarantee views. These matter as much:

- **Visibility:** a private or unlisted upload gets 0 views. ClipForge now checks this after every upload.
- **Reused content:** clips of other creators' streams (for example a famous streamer's GTA marathon) compete with hundreds of other clippers, can be claimed or muted by Content ID, and YouTube can limit reach or monetisation of "reused content". Add something of your own (commentary, a reaction, a top-5 edit, context in the hook), credit the creator, and prefer creators who allow clipping (see **Earn**).
- **A new channel** starts with little trust. Shorts are first shown to a small test audience; the share who keep watching instead of swiping away decides whether it is pushed further. Expect the first few weeks to be slow.
- **One niche per channel**, so the audience YouTube finds for one clip likes the next one too.
- **No duplicates:** one strong clip per moment, spaced out (ClipForge does this, see [SEO rules](#seo-rules)).
- **Consistency:** 1-3 clips every day, at the times your viewers are online (the schedule dialog has quick picks).
- **Early engagement:** reply to the first comments, pin a question.
- **Trending topics:** clip moments people are searching for this week.

## SEO rules

What ClipForge writes for every clip, and why (from YouTube's own guidance and a review of a real channel):

| Rule | Why |
|---|---|
| Names spelled exactly as in the original video ("Kai Cenat", "Doomsday") | A misspelled name in a title or description loses the search for the real name. ClipForge spell-checks the AI's text against the original video title, the creator's name and a list of well-known names |
| Misspellings only in tags ("kai centa", "kai cenant") | Tags are mainly for catching people who mistype a name. Never in the title |
| First ~150 characters read like ad copy and contain the main keyword | That is what shows in search results and under the Short |
| No keyword lists ("If you search for…", 🔎 lists) | YouTube treats them as keyword stuffing and can limit or remove the video |
| 3 hashtags, specific ones (#KaiCenat #GTA6 #GTA6FunnyMoments) | The first 3 show above the title; with more than 15, YouTube ignores all of them. #fyp and #foryou are TikTok habits and do nothing on YouTube |
| No "#Shorts" in the title | YouTube finds Shorts by length and shape; the hashtag only wastes title characters |
| 10-15 tags, no repeated words, under 500 characters | "gta 6 funny moments funny moments" looks broken and spammy |
| A category that matches the content | A movie trailer in Gaming confuses who YouTube shows it to |
| A different title for every clip, spaced-out uploads | Six near-identical Shorts in seven minutes look like duplicate spam. Space them out, or merge them into one compilation |

Things only you can do on the channel: write the **About** section and **channel keywords** in YouTube Studio around one niche, keep the channel to that niche, and delete or privatise old near-duplicate uploads.

## Client pitch deck

[`pitch-deck/`](pitch-deck/) holds a 24-slide client pitch deck for selling ClipForge clipping (PowerPoint with Morph transitions and builds, a PDF edition, a design guide, a presenter script, an asset inventory and technical notes), plus the scripts that generate it in `pitch-deck/source/`. Install the fonts in `pitch-deck/fonts/` before presenting, and fill in the contact line on slide 23. All numbers in it are illustrative.

## What is never committed

`.env`, `data/` (videos, settings, database, and tokens in `data/secrets/`), `client_secret*.json`, and any Ollama or model files (`.ollama/`, `models/`, `*.gguf`, `*.safetensors`, `*.bin`) are all in `.gitignore`.

## Run on a server (Docker)

`docker compose up -d --build` starts Redis, the web app and API, workers, and the Telegram poller (which also publishes scheduled clips). Set `API_TOKEN` in `.env`, and keep port 8000 behind Tailscale or an HTTPS tunnel.

## Tests

`pytest -q` makes synthetic videos with ffmpeg, so it needs no network and no accounts.
