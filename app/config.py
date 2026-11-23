"""Environment variables and settings.

All values come from the environment (or a .env file). Nothing here is a secret
default: anything left empty disables the feature that needs it.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: a blank `KEY=` line in .env means "use the default", not "parse an empty string"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # --- storage -----------------------------------------------------------
    data_dir: Path = Path("/data")
    db_path: Path | None = None  # defaults to <data_dir>/jobs.sqlite3

    # --- API security (FIX: the spec exposed an unauthenticated HTTP upload) -
    api_token: str = ""  # required header X-API-Token on every ingest endpoint
    max_upload_mb: int = 2048

    # --- queue -------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_eager: bool = False  # run tasks inline (CLI / tests), no Redis needed
    # celery: Redis + Celery workers (server/Docker)
    # thread: background threads in one process (`python -m app.cli serve`, e.g. on a Mac)
    # inline: run each step synchronously (CLI one-shots, tests)
    task_mode: str = "celery"

    # --- highlight detection / clip shape -----------------------------------
    clip_min_s: float = 15.0
    clip_target_s: float = 30.0
    clip_max_s: float = 300.0  # Shorts count up to 3 min; longer clips upload as normal videos
    clips_per_source: int = 3  # long VODs yield up to N clips
    output_formats: str = "vertical"  # comma list of vertical,portrait,square,landscape
    output_width: int = 1080   # legacy; formats decide the size now
    output_height: int = 1920
    max_output_mb: float = 48.0  # Telegram bot upload limit is 50 MB (bigger clips get a preview copy)
    caption_style: str = "pop"     # pop | classic | minimal | none
    hook_overlay: bool = True      # big hook title for the first seconds
    hook_seconds: float = 3.0
    progress_bar: bool = True
    jump_cuts: bool = True         # remove dead air between sentences (only when there is speech)
    zoom_punch: bool = True        # punch-in zooms on key words
    cover_intro: bool = True       # cover image as the first frames (so every platform shows it as the thumbnail)
    payoff_tease: bool = True      # open with ~2 s of the clip's payoff moment, then play the clip
    payoff_replay: bool = True     # end on a slow-motion, zoomed replay of the payoff (loops back into the tease)
    beat: bool = True              # background beat under the clip, ducked under speech (data/music or built-in)
    beat_volume: float = 0.12      # 0-1 before ducking

    # --- transcription ------------------------------------------------------
    whisper_model: str = "small"  # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"  # cpu / cuda / auto
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None  # None = auto-detect
    captions_enabled: bool = True
    caption_words_per_line: int = 3
    caption_font: str = "DejaVu Sans"

    # --- live streams -------------------------------------------------------
    # Live streams are not recorded in full: only a rolling buffer of the last LIVE_BUFFER_S seconds is on disk,
    # and the window around each chat spike (or audio spike, without chat) is saved as a clip.
    live_segment_s: int = 10  # buffer piece length
    live_buffer_s: int = 300
    live_max_hours: float = 6.0
    live_chat: bool = True  # watch YouTube/Twitch live chat for spikes
    live_chat_ratio: float = 2.5  # chat speed vs its recent median that counts as a spike
    live_highlight_ratio: float = 1.6  # fallback: segment audio peak vs rolling median
    # Finished YouTube videos/streams: use the "Most replayed" graph and download only its peaks
    most_replayed: bool = True

    # --- Telegram -------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_owner_chat_id: int = 0  # only this user can approve / submit links
    telegram_webhook_secret: str = ""  # checked against X-Telegram-Bot-Api-Secret-Token

    # --- YouTube --------------------------------------------------------------
    youtube_client_secrets: Path | None = None  # client_secret.json (Desktop app type)
    youtube_privacy: str = "public"  # public | unlisted | private
    youtube_category_id: str = "20"  # fallback only: each clip gets a matching category (trailers -> 1, games -> 20)
    # Clips from the same video posted minutes apart look like duplicate spam: the next one is scheduled this
    # many minutes after the previous one (0 = off). "Publish now anyway" overrides it.
    publish_spacing_min: int = 90

    # --- Instagram ------------------------------------------------------------
    instagram_user_id: str = ""  # IG *Business/Creator* account id
    instagram_access_token: str = ""  # long-lived token (60 days, auto-refreshed)
    instagram_app_id: str = ""  # needed only for automatic token refresh
    instagram_app_secret: str = ""
    graph_api_version: str = "v21.0"

    # --- TikTok (Content Posting API) ---------------------------------------------
    tiktok_privacy: str = "SELF_ONLY"  # unaudited TikTok apps may only post privately

    # --- misc -----------------------------------------------------------------
    default_hashtags: str = "#shorts"
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def mode(self) -> str:
        return "inline" if self.celery_eager else self.task_mode

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw_clips"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed_clips"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def live_dir(self) -> Path:
        return self.data_dir / "live"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def database(self) -> Path:
        return self.db_path or self.data_dir / "jobs.sqlite3"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.processed_dir, self.temp_dir, self.live_dir, self.secrets_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
