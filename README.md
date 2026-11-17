# ClipForge

**Turn any video into viral short clips, on your own computer, for free.**

Paste a video link, upload a file or follow a live stream. ClipForge finds the most exciting moments, cuts them into Shorts / Reels / TikToks with word-by-word captions, a hook title, jump cuts, zoom punch-ins, a progress bar and a cover image, scores how viral each one looks, and sends them to you (web app or Telegram) to approve. Approved clips are posted to YouTube, Instagram and TikTok, on a schedule if you like.

Everything runs locally: speech-to-text with Whisper, the AI writer with Ollama (or any cloud model you choose). No subscriptions.

---

## What it does

| | |
|---|---|
| **Sources** | Video links (YouTube, Twitch, Kick, TikTok, X, Instagram, Reddit, direct .mp4/.m3u8), uploaded files, live streams (cuts clips while the stream is still running) |
| **Shapes** | Vertical 9:16, portrait 4:5, square 1:1, landscape 16:9. Pick any mix per job |
| **Finding moments** | Audio excitement + on-screen motion, optionally re-ranked by the AI from what is said |
| **Viral look** | Pop captions (spoken word highlighted, key words coloured), hook title for the first 3 s, jump cuts (dead air removed), zoom punch-ins on key words, progress bar, your watermark |
| **Smart crop** | Follows faces (streamer cam, podcast guests) and action instead of always cropping the centre |
| **Cover image** | Made for every clip and used as its first frame, so YouTube, Instagram and TikTok all show it as the thumbnail. Change it any time: another moment, other text or your own picture |
| **AI writer** | Hook, title, caption, hashtags and a virality score, plus alternative hooks and titles to choose from |
| **Edit before posting** | Title, hook, caption and cover, in the web app or with buttons under each clip in Telegram |
| **Copyright check** | Every link, live stream and upload gets a risk level (low / medium / high), whether the original is likely monetised, the reasons, and what to do about it |
| **Publishing** | YouTube (Shorts or normal videos), Instagram Reels, TikTok. Publish now or schedule |
| **Earn** | Track paid clipping campaigns (Whop Content Rewards, Vyro, ClipAffiliates, Promote.fun, direct deals): required hashtags are added for you, post links are kept ready to submit, views and estimated earnings are tracked |

---

## 1. Install (Mac)

You need a Mac (Apple Silicon or Intel), about 5 GB free, and [Homebrew](https://brew.sh).

```bash
# Tools: ffmpeg-full has the subtitle library (libass) that captions need.
brew install ffmpeg-full python@3.12 git
brew link --force ffmpeg-full
ffmpeg -filters | grep " ass "          # must print a line, otherwise captions are skipped

# Get ClipForge
git clone https://github.com/amitkumarsingh1697/ClipForge.git
cd ClipForge
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Start it
python -m app.cli serve
```

Your browser opens **http://127.0.0.1:8000**. The first clip downloads the Whisper speech model (~500 MB), so it takes a few minutes; later clips are faster.

To keep it running after you close Terminal:

```bash
nohup python -m app.cli serve > data/serve.log 2>&1 &
```

**Windows / Linux:** install Python 3.12 and ffmpeg (with libass; most Linux ffmpeg packages have it), then follow the same steps. On Windows activate with `.venv\Scripts\activate`.

---

## 2. Make your first clips

1. **Create**: paste a link (or switch to Upload / Live stream). Press **🛡 Check copyright** first if you're not sure you may use it.
2. Pick shapes, how many clips and how long (up to 5 min; Shorts count up to 3 min).
3. Choose the caption style and extras (hook, jump cuts, zooms, progress bar, cover as first frame).
4. **✦ Make clips.** Clips appear under **Clips** in a minute or two.
5. On each clip: **✎ Edit** (title, hook, caption, cover), **✓ Approve** (publish now or schedule), **Reject**, **⬇ Video / Cover**, **↻ Remake** (fresh AI hook and title).

---

## 3. Telegram bot (approve from your phone)

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name and a username ending in `bot`. Copy the token it gives you.
2. Open `.env` in the ClipForge folder and set `TELEGRAM_BOT_TOKEN=` to that token.
3. Run `python -m app.cli telegram-setup`, then send `/start` to your new bot within 5 minutes. It saves your chat id, so **only you** can control the bot.
4. Restart ClipForge (`python -m app.cli serve`).

In the bot:

- Send any video or live link to make clips. Add shape words to choose the shape: `https://youtu.be/... landscape square`.
- `/check <link>`: copyright and monetisation risk before you clip.
- Each finished clip arrives with its cover and buttons: **✅ Approve & Publish**, **❌ Reject & Delete**, **✏️ Title**, **🪝 Hook**, **🖼 Cover**.
  - Title / Hook: tap one of the AI's alternatives, or type your own (send `none` to remove the hook).
  - Cover: send a photo, a number of seconds (e.g. `12`) to use that moment, or text to write on it.

The bot needs no public address (it polls Telegram), so it runs fine on a laptop on home Wi-Fi.

---

## 4. AI writer (optional)

Clips work without any AI. With one, ClipForge writes the hook, title (plus alternatives), caption, hashtags and highlighted key words from what is said, and can help pick the best moments.

**Local and free (recommended):**

```bash
brew install ollama && brew services start ollama
ollama pull llama3.1:8b          # ~5 GB; on 8 GB Macs use llama3.2:3b
```

Then **AI & brand** → Provider **Ollama** → pick the model → **Save** → **Test**. LM Studio, llama.cpp, Jan, vLLM and LocalAI work too.

**Cloud models** need an API key from the provider's developer site (the ChatGPT / Claude / Gemini chat apps themselves can't be connected):

| Provider | Get a key | Notes |
|---|---|---|
| Claude (Anthropic) | console.anthropic.com | |
| ChatGPT (OpenAI) | platform.openai.com/api-keys | |
| Gemini (Google) | aistudio.google.com/apikey | has a free tier |
| Kimi (Moonshot) | platform.moonshot.ai | |
| DeepSeek, Groq, OpenRouter, Mistral | their consoles | Groq and OpenRouter have free options |

Each provider keeps its own key, so you can switch back and forth. Under **Your brand** set a watermark (your @handle on every clip) and default hashtags.

---

## 5. Connect your accounts (auto-posting)

Until an account is connected, **Approve** just marks the clip approved so you can download it and post it yourself. All of this is under **Publishing** in the app.

### YouTube

1. [Google Cloud Console](https://console.cloud.google.com/projectcreate): create a project (free).
2. Enable **[YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)**.
3. **Google Auth Platform / OAuth consent screen**: External, app name, your email, add your Google account as a test user, then **Publish app** (In production). In Testing mode the sign-in expires every 7 days.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
5. In ClipForge: upload the JSON, press **Connect YouTube**, sign in and allow. (If Google says the app is unverified: Advanced → continue. It's your own app.)

New Google projects can only post **private** videos until Google audits them; switch them to public in YouTube Studio. Clips up to 3 min that aren't landscape are posted as Shorts. The free quota allows about 6 uploads a day.

### Instagram Reels

1. Make your Instagram a **Professional** (Business or Creator) account and link it to a Facebook Page.
2. Create an app at [Meta for Developers](https://developers.facebook.com/apps) and add the Instagram product.
3. Generate a long-lived access token with `instagram_basic`, `instagram_content_publish` and `pages_show_list`, and copy your Instagram account ID.
4. Paste the ID and token (and optionally the app ID / secret for automatic token refresh) and press **Save & check**.

### TikTok

1. Create an app at [TikTok for Developers](https://developers.tiktok.com/apps) with **Login Kit** and **Content Posting API** (Direct Post), scope `video.publish`.
2. Sign in with your TikTok account through that app to get an access token and a refresh token.
3. Paste client key, client secret, access token and refresh token and press **Save & check**. Tokens refresh automatically.

Until TikTok audits your app, posts are **private** (only you can see them).

---

## 6. Earn from clipping

Creators and brands pay clippers per 1,000 views. On **Earn**:

1. Open a marketplace (Whop Content Rewards, Vyro, ClipAffiliates, Promote.fun) and join a campaign.
2. **Add a campaign** in ClipForge with its pay rate, required hashtags and @mention.
3. On **Create**, pick the campaign. Its tags go into every caption automatically.
4. After publishing, the post links are listed under the campaign with **Copy** buttons. Submit them on the marketplace.
5. **↻ Refresh views** reads YouTube and Instagram view counts and estimates what you've earned.

None of the marketplaces offer an API for submitting clips yet, so that last step is yours.

**Copyright, honestly:** re-uploading other people's videos (TV, anime, music, sports, other creators) can get Content ID claims or strikes, and channels made only of re-uploads can be refused monetisation as "reused content". Use the copyright check, clip creators who allow or pay for clips, add your own commentary, and credit the original. The check is an estimate from public information, not legal advice.

---

## Updating

```bash
cd ClipForge
git pull
source .venv/bin/activate && pip install -r requirements.txt
pkill -f "app.cli serve"; nohup python -m app.cli serve > data/serve.log 2>&1 &
```

Refresh the browser with Cmd+Shift+R.

## Troubleshooting

| Problem | Fix |
|---|---|
| Captions missing, "ffmpeg has no libass" | `brew install ffmpeg-full && brew link --force ffmpeg-full`, then restart. |
| Captions empty (0 words) | `pip install -r requirements.txt` again (PyAV must be below 19). |
| `SSL: UNEXPECTED_EOF`, or downloads from YouTube/Twitch/Instagram fail | Your network (office or managed Wi-Fi) blocks those sites. Use a hotspot or VPN, or download the video and use **Upload**. |
| Clip length slider stops at 60 s | An old `.env` has `CLIP_MAX_S=60`; set it to `300` and restart. |
| "Connect YouTube, Instagram or TikTok" after Approve | No account connected yet; see section 5. |
| YouTube views don't load | Press Connect YouTube again (reading views needs a permission added in this version). |
| Telegram says the video is too large | Clips over ~48 MB are sent as a smaller preview; the full file stays in the app. |
| Bot doesn't answer | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` in `.env`, then restart. Only the owner's messages are answered. |
| AI test fails (Ollama) | `brew services start ollama`; `ollama list` should show the model. |
| AI test fails (cloud) | Check the API key and that your account has credit or free-tier access. |
| Port 8000 in use | `pkill -f "app.cli serve"` and start again. |
| Logs | `data/serve.log` |

## What is never committed

`.env`, `data/` (videos, settings, database, `data/secrets/` tokens), `client_secret*.json` and any Ollama / model files (`.ollama/`, `models/`, `*.gguf`, `*.safetensors`, `*.bin`) are in `.gitignore`.

## Command line

```bash
python -m app.cli clip "https://www.youtube.com/watch?v=..." --formats vertical,landscape --clips 3
python -m app.cli live "https://www.twitch.tv/CHANNEL" --minutes 20
python -m app.cli telegram-setup
```

## Run on a server (Docker)

`docker compose up -d --build` runs Redis, the API/UI, workers and the Telegram poller (which also publishes scheduled clips). Set `API_TOKEN` in `.env` and keep port 8000 behind Tailscale or an HTTPS tunnel.

## Tests

`pytest -q` (makes synthetic videos with ffmpeg; no network or accounts needed).
