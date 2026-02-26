"""Application configuration - reads from .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)


class Settings:
    # App
    APP_ENV: str = os.getenv("APP_ENV", "development")
    APP_DEBUG: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "change-me")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./data/scholaverse.db"
    )

    # External VM services
    AI_WORKER_BASE_URL: str = os.getenv(
        "AI_WORKER_BASE_URL", "http://192.168.50.110:8000"
    )
    DB_STORAGE_BASE_URL: str = os.getenv(
        "DB_STORAGE_BASE_URL", "http://192.168.50.112"
    )
    USE_MOCK_AI_WORKER: bool = (
        os.getenv("USE_MOCK_AI_WORKER", "true").lower() == "true"
    )
    USE_MOCK_STORAGE: bool = (
        os.getenv("USE_MOCK_STORAGE", "true").lower() == "true"
    )

    # Guest mode (skip auth for demo/preview)
    GUEST_MODE: bool = os.getenv("GUEST_MODE", "false").lower() == "true"

    # Cloudflare
    CF_AUTH_HEADER: str = os.getenv(
        "CF_AUTH_HEADER", "cf-access-authenticated-user-email"
    )

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    STATIC_DIR: Path = BASE_DIR / "app" / "static"
    TEMPLATES_DIR: Path = BASE_DIR / "app" / "templates"


settings = Settings()
