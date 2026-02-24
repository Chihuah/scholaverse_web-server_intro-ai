"""Jinja2 template configuration."""

import json

from fastapi.templating import Jinja2Templates

from app.config import settings

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


templates.env.filters["fromjson"] = _fromjson
