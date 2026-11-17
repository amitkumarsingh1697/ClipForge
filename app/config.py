"""Environment variables and settings.

All values come from the environment (or a .env file). Nothing here is a secret
default: anything left empty disables the feature that needs it.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- storage -----------------------------------------------------------
    data_dir: Path = Path("/data")
    db_path: Path | None = None  # defaults to <data_dir>/jobs.sqlite3

    # --- API security (FIX: the spec exposed an unauthenticated HTTP upload) -
    api_token: str = ""  # required header X-API-Token on every ingest endpoint
    max_upload_mb: int = 2048

    # --- queue -------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_eager: bool = False  # run tasks inline (CLI / tests), no Redis needed

    # --- highlight detection / clip shape -----------------------------------
    clip_min_s: float = 15.0
    clip_target_s: float = 30.0
    clip_max_s: float = 60.0
    clips_per_source: int = 3  # long VODs yield up to N clips
    output_width: int = 1080
    output_height: int = 1920
    max_output_mb: float = 48.0  # Telegram bot upload limit is 50 MB

    # --- transcription ------------------------------------------------------
    whisper_model: str = "small"  # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"  # cpu / cuda / auto
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None  # None = auto-detect
    captions_enabled: bool = True
    caption_words_per_line: int = 3
    caption_font: str = "DejaVu Sans"

    # --- live streams -------------------------------------------------------
    live_segment_s: int = 120
    live_max_hours: float = 6.0
    live_highlight_ratio: float = 1.6  # segment peak vs rolling median to cut a clip

    # --- Telegram -------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_owner_chat_id: int = 0  # only this user can approve / submit links
    telegram_webhook_secret: str = ""  # checked against X-Telegram-Bot-Api-Secret-Token

    # --- YouTube --------------------------------------------------------------
    youtube_client_secrets: Path | None = None  # client_secret.json (Desktop app type)
    youtube_privacy: str = "private"  # private | unlisted | public
    youtube_category_id: str = "20"  # Gaming

    # --- Instagram ------------------------------------------------------------
    instagram_user_id: str = ""  # IG *Business/Creator* account id
    instagram_access_token: str = ""  # long-lived token (60 days, auto-refreshed)
    instagram_app_id: str = ""  # needed only for automatic token refresh
    instagram_app_secret: str = ""
    graph_api_version: str = "v21.0"

    # --- misc -----------------------------------------------------------------
    default_hashtags: str = "#gaming #shorts"

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
