"""Runtime settings for the texting assistant, from environment / .env.

Every secret comes from the environment. Local development works with
no configuration at all: SQLite in ./data, a generated encryption key,
and links pointing at localhost.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # storage
    database_url: str = f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
    encryption_key: str | None = None  # Fernet key; dev generates one in data/ when unset

    # public web (the one-time connect page)
    public_base_url: str = "http://localhost:8000"
    setup_link_minutes: int = 30

    # assistant brain: any OpenAI-compatible endpoint (free presets live in app/ai/llm.py)
    llm_provider: str = "gemini"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None

    # background jobs (briefings, reminders, syncs): on for the deployed web process
    run_scheduler: bool = False

    # product
    assistant_name: str = "Assistant"
    default_timezone: str = "America/Toronto"
    terms_version: str = "2026-10-beta"


@lru_cache
def get_settings() -> Settings:
    return Settings()
