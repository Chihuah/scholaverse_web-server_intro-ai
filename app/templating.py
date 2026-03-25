"""Jinja2 template configuration."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import settings

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))


def _fromjson(value: str | None) -> dict:
    """Parse a JSON string into a dict; returns {} on failure or None input."""
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _format_taipei(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a UTC datetime to Asia/Taipei and format it."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TAIPEI_TZ).strftime(fmt)


templates.env.filters["fromjson"] = _fromjson
templates.env.filters["format_taipei"] = _format_taipei
