# gta-video-agent

Turns gameplay (phone uploads, video links, or live streams) into 1080×1920 captioned Shorts/Reels, asks for approval on Telegram, then publishes to YouTube and Instagram. Free, local models only. Design and fix log: `GTA_6_Video_Automation_Architecture.md`.

## Try it without any accounts
```bash
sudo apt install ffmpeg fonts-dejavu-core
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DATA_DIR=./data python -m app.cli clip "https://www.youtube.com/watch?v=..."   # or a local .mp4
DATA_DIR=./data python -m app.cli live "https://www.twitch.tv/<channel>" --minutes 20
```
Clips land in `data/processed_clips/`. First run downloads the Whisper model (~500 MB for `small`).

## Run the server
```bash
cp .env.example .env    # set API_TOKEN, TELEGRAM_*, and the publisher you want
docker compose up -d --build
docker compose run --rm -p 8765:8765 api python scripts/youtube_auth.py   # once, via ssh -L 8765:localhost:8765
curl -H "X-API-Token: $API_TOKEN" -H 'content-type: application/json' \
     -d '{"url":"https://..."}' http://<tailscale-ip>:8000/ingest/url
```
Or just send a link to your Telegram bot.

## Tests
`pytest -q` (generates synthetic videos with ffmpeg; no network or accounts needed).
