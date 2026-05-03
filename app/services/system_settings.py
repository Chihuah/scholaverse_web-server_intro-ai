from __future__ import annotations

"""Helpers for persisted global system settings."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

SYSTEM_SETTING_DEFAULTS: dict[str, str] = {
    "ollama_model": "gemma4:e4b",
    "image_backend": "local",
}

SYSTEM_SETTING_LABELS: dict[str, str] = {
    "ollama_model": "OLLAMA_MODEL",
    "image_backend": "IMAGE_BACKEND",
}

SYSTEM_SETTING_ALLOWED_KEYS = set(SYSTEM_SETTING_DEFAULTS.keys())

OLLAMA_MODEL_SUGGESTIONS = [
    "gemma4:e4b",
    "qwen2.5-14b:latest",
    "qwen2.5:14b-instruct",
    "frob/qwen3.5-instruct:9b",
]

# Allowed values for the global image-generation backend toggle. "local" routes
# every student request through the on-prem Stable Diffusion (and image edit is
# unavailable). "cloud" routes through OpenAI gpt-image-2 with image edit
# support for keeping character consistency across cards.
IMAGE_BACKEND_OPTIONS = ("local", "cloud")


async def get_system_setting(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None or row.value == "":
        return SYSTEM_SETTING_DEFAULTS.get(key, "")
    return row.value


async def get_system_settings_map(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(SystemSetting))
    rows = {row.key: row.value for row in result.scalars().all()}
    merged = dict(SYSTEM_SETTING_DEFAULTS)
    merged.update({k: v for k, v in rows.items() if v is not None})
    return merged


async def set_system_setting(db: AsyncSession, key: str, value: str) -> SystemSetting:
    if key not in SYSTEM_SETTING_ALLOWED_KEYS:
        raise ValueError(f"Unsupported system setting key: {key}")

    normalized = value.strip()
    if not normalized:
        normalized = SYSTEM_SETTING_DEFAULTS.get(key, "")

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=key, value=normalized)
        db.add(row)
    else:
        row.value = normalized
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row
