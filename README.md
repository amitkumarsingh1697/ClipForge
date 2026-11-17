# Clip Agent

Turns any video (a link, an uploaded file, or a live stream) into short captioned clips in the shapes you pick: **vertical 9:16, portrait 4:5, square 1:1, landscape 16:9**. It finds the most exciting moments by itself, lets you review them in a web UI or on Telegram, then publishes to YouTube and Instagram if you connect them. Free, local models only. It works for any video; the original design notes (`GTA_6_Video_Automation_Architecture.md`) started with GTA gameplay.

## Run it on a Mac
```bash
brew install ffmpeg-full python@3.12 && brew link --force ffmpeg-full
# Homebrew's plain `ffmpeg` has no libass, so captions would be skipped. Check: ffmpeg -filters | grep " ass "
git clone https://github.com/amitkumarsingh1697/ClippingTool.git && cd ClippingTool
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m app.cli serve          # opens http://127.0.0.1:8000
```
Paste a link, pick formats, caption style and length (up to 5 min; Shorts count up to 3 min), press **Make clips**. Each clip gets the viral-tool treatment: word-by-word highlighted captions, a hook title for the first 3 seconds, a progress bar, a virality score and a cover image. With a local LLM the hook, title, caption, hashtags and highlighted keywords are written from what is said. Clips appear below with Approve / Reject / Download. The first run downloads the Whisper caption model (~500 MB).

## Connect Telegram
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...`.
3. Run `python -m app.cli telegram-setup` and send `/start` to your new bot. Your chat id is saved automatically.
4. Restart `python -m app.cli serve`. Now send the bot any link (add words like `landscape square` to choose shapes) and approve clips from your phone.

## Local LLM (optional)
No LLM is needed to make clips: highlights come from audio and motion analysis, and captions from local Whisper. A local LLM adds titles, captions and hashtags written from what is said, and can help pick the best moments.
```bash
brew install ollama && brew services start ollama
ollama pull llama3.1:8b
```
Then open **AI settings** in the UI, choose Ollama and the model, and press Save, then Test LLM. LM Studio, llama.cpp and other OpenAI-compatible servers work too (URL like `http://localhost:1234/v1`). The Whisper model can be changed there as well.

## Publishing
Open **Publishing** in the UI. Until an account is connected, Approve just marks the clip approved so you can download it.

### YouTube (Shorts or normal videos)
1. Go to https://console.cloud.google.com, create a project (any name).
2. **APIs & Services > Library**: enable **YouTube Data API v3**.
3. **OAuth consent screen**: External, fill in app name and your email, add your Google account as a test user, then **Publish app** (In production). Testing mode tokens expire after 7 days.
4. **Credentials > Create credentials > OAuth client ID > Desktop app**. Download the JSON.
5. In the UI, upload that JSON and press **Connect YouTube**, then sign in and allow access. The token is saved in `secrets/` (never committed).

Clips up to 3 min in vertical/portrait/square post as Shorts; longer or landscape clips post as normal videos.

### Instagram Reels
Needs an Instagram **Business or Creator** account linked to a Facebook Page, and a Meta developer app (https://developers.facebook.com) with the Instagram Graph API. Generate a long-lived user token with `instagram_basic`, `instagram_content_publish` and `pages_show_list`, then paste the app id, app secret and token into the Instagram form and press Save. The app checks the token and refreshes it automatically.

## Update to the latest code
```bash
cd ClippingTool && git pull && source .venv/bin/activate && pip install -r requirements.txt
pkill -f "app.cli serve"; nohup python -m app.cli serve > data/serve.log 2>&1 &
```
Then refresh the browser with Cmd+Shift+R. Logs are in `data/serve.log`.

## Troubleshooting
| Problem | Fix |
|---|---|
| Captions missing, "ffmpeg has no libass" | `brew install ffmpeg-full && brew link --force ffmpeg-full`, then restart. |
| Captions empty (0 words) | Reinstall requirements: `pip install -r requirements.txt` (PyAV must be below 19). |
| `SSL: UNEXPECTED_EOF` or download fails for YouTube/Twitch/Instagram | Your network (office/managed Wi-Fi) blocks those sites. Use a hotspot or VPN, or download the video and use the Upload tab. |
| "Connect YouTube or Instagram" after Approve | No publisher connected yet; see Publishing above. |
| Telegram says the video is too large | Clips over ~48 MB are sent as a smaller preview; the full file is in the UI. |
| LLM test fails | Check `ollama list` shows the model and `brew services start ollama` is running. |
| Port 8000 in use | `pkill -f "app.cli serve"` and start again. |

## What is never committed
`.env`, `secrets/`, `data/` (videos, settings, database) and any Ollama/model files (`.ollama/`, `models/`, `*.gguf`, `*.safetensors`, `*.bin`) are in `.gitignore`.

## Command line
```bash
python -m app.cli clip "https://www.youtube.com/watch?v=..." --formats vertical,landscape --clips 3
python -m app.cli live "https://www.twitch.tv/CHANNEL" --minutes 20
```

## Server (Docker)
`docker compose up -d --build` runs Redis, the API/UI, workers and the Telegram poller. Set `API_TOKEN` in `.env` and keep port 8000 behind Tailscale or an HTTPS tunnel.

## Tests
`pytest -q` (generates synthetic videos with ffmpeg; no network or accounts needed).
