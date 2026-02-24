"""Internal API routes — VM-to-VM callbacks.

POST /api/internal/generation-callback — AI worker completion callback
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.card import Card


def _image_path_to_url(image_path: str | None) -> str | None:
    """Convert ai-worker image_path (e.g. /students/123/cards/card_042.png)
    to a proxy URL via ai-worker's /api/images/ endpoint."""
    if not image_path:
        return None
    stripped = image_path.lstrip("/")
    base = settings.AI_WORKER_BASE_URL.rstrip("/")
    return f"{base}/api/images/{stripped}"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal"])


class GenerationCallbackBody(BaseModel):
    """Request body from vm-ai-worker when generation completes."""

    job_id: str
    card_id: int
    status: str  # "completed" or "failed"
    image_path: str | None = None
    thumbnail_path: str | None = None
    generated_at: str | None = None
    error: str | None = None


@router.post("/generation-callback")
async def generation_callback(
    body: GenerationCallbackBody,
    db: AsyncSession = Depends(get_db),
):
    """Handle ai-worker generation completion callback.

    Updates the Card row with image URLs and status.
    This endpoint is in PUBLIC_PATHS (no auth required) since
    it's called by vm-ai-worker internally.
    """
    result = await db.execute(select(Card).where(Card.id == body.card_id))
    card = result.scalar_one_or_none()
    if card is None:
        logger.warning("Callback for unknown card_id=%d, job=%s", body.card_id, body.job_id)
        raise HTTPException(status_code=404, detail="Card not found")

    if body.status == "completed":
        card.status = "completed"
        card.image_url = _image_path_to_url(body.image_path)
        card.thumbnail_url = _image_path_to_url(body.thumbnail_path)
        if body.generated_at:
            try:
                card.generated_at = datetime.fromisoformat(body.generated_at)
            except ValueError:
                card.generated_at = datetime.now(timezone.utc)
        else:
            card.generated_at = datetime.now(timezone.utc)
        logger.info("Card %d generation completed (job %s)", body.card_id, body.job_id)
    else:
        card.status = "failed"
        logger.warning(
            "Card %d generation failed (job %s): %s",
            body.card_id, body.job_id, body.error,
        )

    await db.commit()
    return {"status": "ok", "card_id": body.card_id}
