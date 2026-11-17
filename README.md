# Clip Agent

Turns any video (a link, an uploaded file, or a live stream) into short captioned clips in the shapes you pick: **vertical 9:16, portrait 4:5, square 1:1, landscape 16:9**. It finds the most exciting moments by itself, lets you review them in a web UI or on Telegram, then publishes to YouTube and Instagram if you connect them. Free, local models only. It works for any video; the original design notes (`GTA_6_Video_Automation_Architecture.md`) started with GTA gameplay.

## Run it on a Mac
```bash
brew install ffmpeg python@3.12
git clone https://github.com/amitkumarsingh1697/ClippingTool.git && cd ClippingTool
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m app.cli serve          # opens http://127.0.0.1:8000
```
Paste a link, pick formats, press **Make clips**. Clips appear below with Approve / Reject / Download. The first run downloads the Whisper caption model (~500 MB).

## Connect Telegram
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...`.
3. Run `python -m app.cli telegram-setup` and send `/start` to your new bot. Your chat id is saved automatically.
4. Restart `python -m app.cli serve`. Now send the bot any link (add words like `landscape square` to choose shapes) and approve clips from your phone.

## Command line
```bash
python -m app.cli clip "https://www.youtube.com/watch?v=..." --formats vertical,landscape --clips 3
python -m app.cli live "https://www.twitch.tv/CHANNEL" --minutes 20
```

## Server (Docker)
`docker compose up -d --build` runs Redis, the API/UI, workers and the Telegram poller. Set `API_TOKEN` in `.env` and keep port 8000 behind Tailscale or an HTTPS tunnel.

## Tests
`pytest -q` (generates synthetic videos with ffmpeg; no network or accounts needed).
