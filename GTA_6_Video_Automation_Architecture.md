# Project: Local AI Video Processing & Publishing Agent
**Objective:** Build an automated, self-hosted pipeline on an Ubuntu server that ingests GTA 6 gameplay from **three sources** (a clip uploaded from a mobile device, an existing video link, or a live stream), automatically finds the best moments, edits them into 9:16 vertical clips with captions, requests user approval via a messaging bot, and publishes to YouTube Shorts and Instagram Reels.

**Constraint:** ZERO paid subscriptions. All AI models must run locally.

> **Revision note (v2).** v1 could not work end to end: the crop filter fails on most inputs, the Telegram webhook and YouTube OAuth callback cannot be reached on `http://STATIC_IP`, Instagram cannot take a local file the way v1 assumed, nothing linked a Telegram tap to a job, and only phone uploads were supported. Every change is listed in §6.

---

## 1. System Architecture & Tech Stack

### Core Technologies (100% Free / Open Source)
*   **API & Ingestion:** Python + FastAPI. Every ingest endpoint requires an `X-API-Token` header. *(changed)*
*   **Source download:** `yt-dlp` for video links (YouTube, Twitch, Kick, X, Reddit, direct .mp4/.m3u8). *(new)*
*   **Live capture:** `ffmpeg` segment recorder fed by `yt-dlp` stream resolution. *(new)*
*   **Task Queue:** Celery + Redis, with a separate `live` queue for long-running recorders. *(changed)*
*   **Job state:** SQLite (`/data/jobs.sqlite3`): one row per source and per clip, compare-and-set status changes. *(new)*
*   **Video Editing:** FFmpeg via `subprocess` (crop/pad to 9:16, scale to 1080×1920, trim, burn captions, loudness-normalise).
*   **Transcription/Captions:** `faster-whisper` with VAD, speech-band pre-filter and confidence filtering; word timestamps written to `.ass`. *(changed)*
*   **Highlight Detection:** `librosa` (loudness + onset strength) + OpenCV (motion) for both cut points *and* crop position. *(changed)*
*   **Approval Gateway:** Telegram Bot API via **long polling** (no public HTTPS needed); webhook optional. *(changed)*
*   **Publishing:** YouTube Data API v3 (resumable upload) + Instagram Graph API (Reels, **resumable upload**, Business/Creator account). *(changed)*
*   **Network:** Phone → server over Tailscale (free tier) or an HTTPS tunnel, not an open port on a static IP. *(new)*

---

## 2. Pipeline / Data Flow

**Phase 1: Ingestion (three sources)**
1. **Phone upload:** an iOS/Android Shortcut `POST`s the file to `/upload` with `X-API-Token`. The file is streamed to `/data/raw_clips/` (size-capped, extension-checked).
2. **Video link:** `POST /ingest/url {"url": ...}`, or simply share the link to the Telegram bot. `yt-dlp` downloads ≤1080p MP4 to `/data/raw_clips/`. A link that turns out to be live is routed to the recorder.
3. **Live stream:** `POST /ingest/live {"url": ...}`. ffmpeg stream-copies the stream into 2-minute MP4 segments under `/data/live/<job_id>/` (reconnects on drops; stop with `POST /ingest/live/<id>/stop` or after `LIVE_MAX_HOURS`).
4. Each source becomes a `source` job in SQLite and is queued in Celery.

**Phase 2: The "Agent" Processing (Celery Worker)**
1. **Highlight Extraction:** every 0.5 s of audio is scored (0.6 × loudness z-score + 0.4 × onset strength, blended 75/25 with on-screen motion). A window of `CLIP_TARGET_S` (default 30 s, clamped to 15–60 s) slides over the curve; the top `CLIPS_PER_SOURCE` non-overlapping windows become `clip` jobs, with edges snapped to the quietest moment within ±1.5 s. Inputs already ≤60 s are kept whole. **Live:** each finished segment is scored; a segment whose 95th-percentile loudness is > `LIVE_HIGHLIGHT_RATIO` × the median of the previous 10 becomes a clip while recording continues.
2. **Formatting:** landscape sources are cropped to an **even** width `2·⌊ih·9/32⌋`, at the x offset that contains the most motion (with a centre prior), then scaled to 1080×1920. Portrait/square sources are fitted over a blurred background instead of cropped.
3. **Transcription:** the clip's audio is band-passed (120 Hz–4 kHz) and denoised, then `faster-whisper` runs with `vad_filter=True` and word timestamps. Segments with `no_speech_prob > 0.6`, `avg_logprob < -1.0`, or known hallucination phrases are dropped. No speech means no captions, not fake ones. Caption failure never fails the clip.
4. **Burn-in:** word groups of 3, uppercase, bold white with black outline, placed above the Shorts/Reels UI, from an `.ass` file. Audio is normalised to −14 LUFS, video is H.264 yuv420p with `+faststart`, bitrate-capped to stay under 48 MB. Output: `/data/processed_clips/<clip_id>.mp4`.

**Phase 3: Approval Loop**
1. The worker sends the clip with `sendVideo` (checked < 50 MB) to `TELEGRAM_OWNER_CHAT_ID`.
2. Inline keyboard: `[✅ Approve & Publish]` (`a:<clip_id>`) and `[❌ Reject & Delete]` (`r:<clip_id>`), well inside the 64-byte `callback_data` limit.
3. The clip row sits in `awaiting_approval` in SQLite; nothing blocks while waiting.
4. Without Telegram, `POST /jobs/<id>/approve|reject` does the same.

**Phase 4: Publishing**
1. User taps Approve. The **long-poller** (`telegram-poller` service) receives it, or the HTTPS webhook if one is configured. Taps from anyone but the owner are ignored.
2. `awaiting_approval → publishing` is an atomic compare-and-set, so a double tap publishes once.
3. The publish task uploads to every configured platform that hasn't already succeeded for this clip (retries are idempotent).
4. YouTube: resumable upload, `#Shorts` in title, category Gaming, privacy from config. Instagram: create a `REELS` container with `upload_type=resumable`, upload bytes to `rupload.facebook.com`, poll `status_code` until `FINISHED`, then `media_publish`.
5. Success links or a precise error (e.g. quota reached) are sent back via Telegram; `POST /jobs/<id>/retry` re-runs failures.

---

## 3. Directory Structure

```text
/gta-video-agent
│
├── /app
│   ├── main.py                 # FastAPI: upload, URL/live ingest, job status, approve/reject/retry, webhook
│   ├── celery_worker.py        # Celery app + tasks (render queue and `live` queue)
│   ├── config.py               # Environment variables and settings
│   ├── pipeline.py             # (new) job orchestration, Celery-independent
│   ├── cli.py                  # (new) run without Redis/Celery; Telegram long-poller
│   │
│   ├── /services
│   │   ├── ingest.py           # (new) yt-dlp downloads, live segment recorder
│   │   ├── video_editor.py     # FFmpeg crop/pad, smart crop position, render
│   │   ├── transcriber.py      # faster-whisper + .ass caption writer
│   │   ├── highlight_ai.py     # Audio/visual peak detection for cuts
│   │   ├── telegram_bot.py     # sendVideo, callbacks, long polling, link intake
│   │   └── social_publisher.py # YouTube and Instagram API logic
│   │
│   ├── /utils
│   │   ├── auth_helpers.py     # OAuth token storage/refresh (YouTube, Instagram)
│   │   ├── job_store.py        # (new) SQLite job state
│   │   └── media.py            # (new) ffprobe/ffmpeg helpers
│
├── /scripts/youtube_auth.py    # (new) one-time YouTube loopback OAuth
├── /tests                      # (new) synthetic-video tests for every stage
├── /data
│   ├── /raw_clips              # Uploads and downloads
│   ├── /live                   # (new) live stream segments
│   ├── /processed_clips        # Final videos waiting for approval
│   ├── /temp                   # Audio extracts, captions
│   └── /secrets                # (new) OAuth tokens (chmod 600)
│
├── Dockerfile                  # (new) python + ffmpeg/libass + DejaVu font
├── docker-compose.yml          # redis, api, worker, worker-live, telegram-poller
├── requirements.txt
└── .env.example
```

## 4. Key Endpoints

All except `/health` and `/telegram-webhook` require `X-API-Token`.

1. `POST /upload`: multipart video. Returns `{"status": "processing", "job_id": "..."}`.
2. `POST /ingest/url` `{"url", "title"?}`: clip an existing video link. *(new)*
3. `POST /ingest/live` `{"url", "title"?}` and `POST /ingest/live/{id}/stop`: record and auto-clip a live stream. *(new)*
4. `GET /jobs/{id}`: status of a source and its clips. `POST /jobs/{id}/approve | reject | retry`. *(new)*
5. `POST /telegram-webhook`: optional, only with HTTPS; verifies `X-Telegram-Bot-Api-Secret-Token`. Default is long polling.
6. `GET /auth/youtube`: reports whether YouTube is authorised. The OAuth itself runs once via `scripts/youtube_auth.py` (loopback on localhost). `GET /auth/youtube/callback` is retired (see §6).

## 5. Development & Operations Notes
*   **Dockerize everything:** `docker compose up -d` starts redis, api, worker, worker-live and telegram-poller, with `./data` mounted at `/data`. Whisper models cache in `/data/models`.
*   **FFmpeg:** use `subprocess`. Do **not** use `-vf "crop=ih*9/16:ih"` (odd width, blind centre crop, no scaling, fails on portrait input); see `video_editor.vertical_filter`.
*   **Authentication & state:** SQLite for jobs, JSON files in `/data/secrets` for tokens. Back up `/data/jobs.sqlite3` and `/data/secrets`.
*   **YouTube setup:** OAuth client type *Desktop app*; consent screen set to *In production* (refresh tokens from *Testing* expire in 7 days). Quota ≈ 6 uploads/day. Unaudited projects upload as private.
*   **Instagram setup:** Business or Creator account linked to a Facebook Page; long-lived token (60 days, refreshed after 30 when `INSTAGRAM_APP_ID/SECRET` are set).
*   **Rights:** only clip streams/videos you own or have permission to repost.

---

## 6. Issues Found in v1 and Fixes

| # | Issue | Cause | Fix |
|---|---|---|---|
| 1 | Render fails on most 16:9 inputs | `crop=ih*9/16:ih` gives 607.5 px for 1080p; x264 + yuv420p needs even width | Width `2·⌊ih·9/32⌋` (606) |
| 2 | Action cropped out of frame | v1 always crops the centre | Motion-energy crop position with centre prior |
| 3 | Low-res Shorts | No scale step: 720p source → 404×720 | Scale to 1080×1920 |
| 4 | Crash on vertical phone recordings / vertical links | Crop width exceeds input width | Fit over blurred background |
| 5 | Garbage captions on gameplay | Whisper hallucinates on gunfire/music | Speech-band filter, VAD, confidence filtering, skip when no speech |
| 6 | Captions small and late | Sentence-level `.srt` | Word-timestamp `.ass` groups, 1080×1920 styling |
| 7 | Highlight step undefined | No window rule, no handling of <15 s clips, one clip per source | Scored sliding window, top-N, edge snapping, short inputs kept whole |
| 8 | Telegram tap never reaches server | Webhooks need HTTPS on 443/80/88/8443; v1 used `http://IP:8000` | Long polling by default; webhook only with HTTPS + secret token |
| 9 | Telegram send fails on longer clips | Bot uploads are capped at 50 MB | Bitrate budget keeps renders < 48 MB; size checked |
| 10 | "The server waits": no link from tap to job | No state store | SQLite jobs table; `callback_data` = `a:<id>` |
| 11 | Double tap publishes twice | No idempotency | Atomic status transition; per-platform publish skip on retry |
| 12 | Anyone can approve / upload | No auth on bot or HTTP API | Owner chat id check; `X-API-Token`; Tailscale recommended |
| 13 | Upload can exhaust RAM/disk | Unbounded upload | Chunked streaming, size cap, extension check |
| 14 | YouTube OAuth callback impossible | Google rejects bare-IP http redirect URIs | One-time loopback flow script |
| 15 | YouTube stops after a week | *Testing* consent screen tokens expire in 7 days | Documented *In production*; refresh + persist |
| 16 | YouTube quota/private surprises | 10k units/day ≈ 6 uploads; unaudited apps forced private | Clear errors, configurable privacy |
| 17 | Instagram publish impossible as written | Graph API can't take a local file; needs Business/Creator account; container must finish processing | Resumable upload + status polling; account requirement documented |
| 18 | Instagram stops after 60 days | Long-lived token expiry | Auto-refresh when >30 days old |
| 19 | Long jobs can starve or be lost | Default Celery prefetch/acks | `acks_late`, prefetch 1, separate `live` queue |
| 20 | "No databases" vs "simple SQLite" | Contradiction | SQLite chosen |
| 21 | **Gap:** no video-link or live-stream input | v1 only accepted phone uploads | `yt-dlp` link ingestion, live segment recorder with rolling highlight detection |

---

## 7. Task List (ordered)

| # | Task | Status |
|---|---|---|
| 1 | Settings, SQLite job store, ffmpeg helpers | Done |
| 2 | Link ingestion via yt-dlp (+ live detection) | Done, tested |
| 3 | Highlight detection (audio + motion, top-N, snapping) | Done, tested |
| 4 | 9:16 render: even crop, smart position, portrait fit, scale, loudnorm, size cap | Done, tested |
| 5 | faster-whisper captions with hallucination filtering + `.ass` burn-in | Done; filter and burn-in tested, live model run not tested (model download blocked in build sandbox) |
| 6 | Pipeline orchestration + Celery tasks + CLI | Done, tested (eager mode) |
| 7 | FastAPI endpoints with token auth | Done, tested |
| 8 | Telegram approval (long polling, owner check, link intake) | Done; logic tested with mocked API |
| 9 | Live-stream recorder with rolling highlight detection | Done, tested on a simulated stream |
| 10 | YouTube resumable upload + loopback OAuth | Done; needs real credentials to verify |
| 11 | Instagram resumable Reels upload + token refresh | Done; flow tested with mocked Graph API |
| 12 | Docker image + compose | Written; not built in sandbox |
| 13 | First real run: build image, authorise YouTube/IG, clip a real link | Next (needs your server + accounts) |
| 14 | Live: stitch highlights that cross a segment boundary (concat neighbours) | Todo |
| 15 | Dynamic panning crop (follow action within a clip, smoothed) | Todo |
| 16 | Chat-activity signal for Twitch/YouTube live (message rate spikes) | Todo |
| 17 | Auto title/hook text from transcript; thumbnail frame pick | Todo |
| 18 | Disk retention: delete raw/live segments after N days | Todo |
