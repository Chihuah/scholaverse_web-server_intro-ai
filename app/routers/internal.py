"""Internal API routes — VM-to-VM callbacks and image proxy.

POST /api/internal/generation-callback — AI worker completion callback
GET  /api/images/proxy/{path}          — Proxy images from internal VMs over HTTPS
GET  /api/images/card/{card_id}        — Anonymous image proxy for hall page (hides student ID)
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.card import Card


def _image_path_to_url(image_path: str | None, *, version: str | None = None) -> str | None:
    """Convert ai-worker image_path to a browser-safe URL with cache-busting."""
    if not image_path:
        return None

    url = image_path if image_path.startswith("/static/") else f"/api/images/proxy/{image_path.lstrip('/')}"
    if version:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}{urlencode({'v': version})}"
    return url

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
    prompt: str | None = None
    final_prompt: str | None = None
    llm_model: str | None = None
    lora_used: str | None = None
    seed: int | None = None
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
        cache_version = body.job_id or body.generated_at or str(datetime.now(timezone.utc).timestamp())
        card.image_url = _image_path_to_url(body.image_path, version=cache_version)
        card.thumbnail_url = _image_path_to_url(body.thumbnail_path, version=cache_version)
        if body.prompt:
            card.prompt = body.prompt
        if body.final_prompt:
            card.final_prompt = body.final_prompt
        if body.llm_model:
            card.llm_model = body.llm_model
        if body.lora_used is not None:
            card.lora_used = body.lora_used
        if body.seed is not None:
            card.seed = body.seed
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


image_proxy_router = APIRouter(prefix="/api/images", tags=["images"])


@image_proxy_router.get("/proxy/{path:path}")
async def proxy_image(path: str):
    """Proxy images from internal VMs so browsers can load them over HTTPS.

    Tries db-storage first, falls back to ai-worker.
    """
    urls = [
        f"{settings.DB_STORAGE_BASE_URL.rstrip('/')}/api/images/{path}",
        f"{settings.AI_WORKER_BASE_URL.rstrip('/')}/api/images/{path}",
    ]
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "image/png")
                    return Response(
                        content=resp.content,
                        media_type=content_type,
                        headers={
                            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                            "Pragma": "no-cache",
                            "Expires": "0",
                        },
                    )
            except httpx.HTTPError:
                continue
    raise HTTPException(status_code=404, detail="Image not found")


@image_proxy_router.get("/card/{card_id}")
async def proxy_card_image(card_id: int, db: AsyncSession = Depends(get_db)):
    """大廳用的匿名圖片代理：以 card ID 取代含學號的路徑，外部看不到學號。

    內部仍透過 VM 內網路徑存取 db-storage，學號路徑不對外暴露。
    """
    result = await db.execute(select(Card).where(Card.id == card_id))
    card = result.scalar_one_or_none()
    if card is None or card.image_url is None:
        raise HTTPException(status_code=404, detail="Card not found")

    # 從 image_url（如 /api/images/proxy/students/xxx/cards/yyy.png?v=...）
    # 提取實際圖片路徑 students/xxx/cards/yyy.png
    parsed = urlparse(card.image_url)
    raw_path = parsed.path
    proxy_prefix = "/api/images/proxy/"
    if raw_path.startswith(proxy_prefix):
        image_path = raw_path[len(proxy_prefix):]
    else:
        image_path = raw_path.lstrip("/")

    # 向 db-storage / ai-worker 取圖（與 proxy_image 相同邏輯）
    urls = [
        f"{settings.DB_STORAGE_BASE_URL.rstrip('/')}/api/images/{image_path}",
        f"{settings.AI_WORKER_BASE_URL.rstrip('/')}/api/images/{image_path}",
    ]
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "image/png")
                    return Response(
                        content=resp.content,
                        media_type=content_type,
                        headers={
                            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                            "Pragma": "no-cache",
                            "Expires": "0",
                        },
                    )
            except httpx.HTTPError:
                continue
    raise HTTPException(status_code=404, detail="Image not found")
