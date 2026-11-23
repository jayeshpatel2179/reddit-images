import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _required(name: str) -> str:
    value = _env(name)
    if not value:
        raise SystemExit(f"Missing required setting {name}. Copy .env.example to .env and fill it in.")
    return value


POST_TYPES = {"video", "animation", "photo", "gallery", "gallery_link", "text"}


def _parse_send_types(value: str) -> frozenset[str]:
    types = frozenset(t.strip().lower() for t in value.split(",") if t.strip())
    unknown = types - POST_TYPES
    if unknown or not types:
        raise SystemExit(f"SEND_TYPES has unknown types {sorted(unknown)}; allowed: {', '.join(sorted(POST_TYPES))}")
    return types


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    subreddit: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str
    reddit_source: str  # "rss" or "api"
    send_types: frozenset[str]  # post kinds to forward; everything else is skipped
    interval_hours: float
    first_run_lookback_hours: float
    overlap_minutes: float
    post_delay_seconds: float
    max_upload_mb: int
    data_dir: Path
    download_dir: Path
    log_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"


def load_config(require_telegram: bool = True) -> Config:
    data_dir = Path(_env("DATA_DIR", "data"))
    download_dir = Path(_env("DOWNLOAD_DIR", "downloads"))
    log_dir = Path(_env("LOG_DIR", "logs"))
    for d in (data_dir, download_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)

    return Config(
        telegram_token=_required("TELEGRAM_BOT_TOKEN") if require_telegram else _env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required("TELEGRAM_CHAT_ID") if require_telegram else _env("TELEGRAM_CHAT_ID"),
        subreddit=_env("SUBREDDIT", "GTA6unmoderated").removeprefix("r/"),
        reddit_client_id=_env("REDDIT_CLIENT_ID"),
        reddit_client_secret=_env("REDDIT_CLIENT_SECRET"),
        reddit_user_agent=_env("REDDIT_USER_AGENT", "python:reddit-telegram-forwarder:v1.0 (by /u/unknown)"),
        reddit_source=_env("REDDIT_SOURCE", "rss").lower(),
        send_types=_parse_send_types(_env("SEND_TYPES", "video")),
        interval_hours=float(_env("INTERVAL_HOURS", "2")),
        first_run_lookback_hours=float(_env("FIRST_RUN_LOOKBACK_HOURS", "2")),
        overlap_minutes=float(_env("OVERLAP_MINUTES", "60")),
        post_delay_seconds=float(_env("POST_DELAY_SECONDS", "3.5")),
        max_upload_mb=int(_env("MAX_UPLOAD_MB", "49")),
        data_dir=data_dir,
        download_dir=download_dir,
        log_dir=log_dir,
    )
