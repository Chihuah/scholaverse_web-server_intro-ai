"""Card generation API routes.

POST /api/cards/generate       — Submit card generation request
GET  /api/cards/generate-info  — Read settings for the generate UI
GET  /api/cards/{id}/status    — Poll generation status
"""

import json
import math
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.card import Card
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.token_transaction import TokenTransaction
from app.models.unit import Unit
from app.services.system_settings import get_system_setting
from app.services import get_ai_worker_service

# Tokens deducted when regenerating a card (first generation is free)
CARD_REGEN_COST = 5

# Allowed values for the global image_backend system setting.
_ALLOWED_BACKENDS = ("local", "cloud")
_DEFAULT_BACKEND = "local"

# Per-card processing seconds for the queue ETA shown on the card-detail page.
# Hard-coded constants — admin/generation-history exposes real elapsed times
# (cards.generated_at - cards.created_at) if you want to recalibrate.
_SECONDS_PER_CARD = {
    "local": 25,
    "cloud": 120,
}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["generation"])


def _resolve_reference_image_url(stored_url: str) -> str:
    """Convert a stored ``cards.image_url`` (relative proxy URL) into an
    absolute URL the ai-worker can fetch directly from db-storage.

    Mirrors ``app.routers.admin._resolve_anchor_image_url`` — kept inline here
    to avoid cross-router imports. If they ever drift, prefer extracting both
    callers to ``app.services.storage``.
    """
    parsed = urlparse(stored_url)
    if parsed.scheme:
        return stored_url
    proxy_prefix = "/api/images/proxy/"
    if parsed.path.startswith(proxy_prefix):
        relative_image_path = parsed.path[len(proxy_prefix):]
        db_base = settings.DB_STORAGE_BASE_URL.rstrip("/")
        return f"{db_base}/api/images/{relative_image_path}"
    web_base = settings.WEB_SERVER_BASE_URL.rstrip("/")
    return f"{web_base}{parsed.path}"


async def _read_image_backend(db: AsyncSession) -> str:
    """Read the global image_backend system setting; default to local."""
    raw = await get_system_setting(db, "image_backend")
    backend = (raw or _DEFAULT_BACKEND).strip().lower()
    if backend not in _ALLOWED_BACKENDS:
        logger.warning(
            "Unknown image_backend setting %r — falling back to %s",
            raw, _DEFAULT_BACKEND,
        )
        backend = _DEFAULT_BACKEND
    return backend


@router.post("/generate")
async def generate_card(
    request: Request,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit a card generation request.

    Gathers the student's current card configs and learning records,
    creates a Card row with status='pending', then submits to ai-worker.

    Body (all optional):
        mode: "keep" | "fresh" — only meaningful when global image_backend is
            "cloud". "keep" routes the request through gpt-image-2 image edit
            using the student's current display card as the reference image
            (locks face/race/body/gender/hair). "fresh" runs cloud generate
            with no reference. Default "fresh".
        seed: int — only used when global image_backend is "local". Cloud
            backend doesn't expose seed, so this is silently ignored there.
    """
    # Parse body (best-effort — empty body == default mode/seed)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    mode_raw = (body.get("mode") or "fresh").strip().lower()
    if mode_raw not in ("keep", "fresh"):
        raise HTTPException(
            status_code=400,
            detail="mode 必須為 'keep' 或 'fresh'",
        )

    seed_raw = body.get("seed")
    requested_seed: int | None = None
    if seed_raw not in (None, "", -1, "-1"):
        try:
            requested_seed = int(seed_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Seed 必須是整數")
        if requested_seed < 0:
            raise HTTPException(status_code=400, detail="Seed 不能為負數")

    image_backend = await _read_image_backend(db)

    # 0. Check if this is a regeneration (user already has non-failed cards)
    existing_result = await db.execute(
        select(Card)
        .where(
            Card.student_id == user.id,
            Card.status.in_(["pending", "generating", "completed"]),
        )
        .limit(1)
    )
    is_regen = existing_result.scalar_one_or_none() is not None
    token_cost = CARD_REGEN_COST if is_regen else 0

    # 409: block duplicate in-flight requests (pending/generating already exists)
    in_flight_result = await db.execute(
        select(Card)
        .where(
            Card.student_id == user.id,
            Card.status.in_(["pending", "generating"]),
        )
        .limit(1)
    )
    if in_flight_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="已有一張卡牌正在生成中，請稍候完成後再重試。",
        )

    if is_regen and user.tokens < token_cost:
        raise HTTPException(
            status_code=400,
            detail=f"代幣不足（重新生成需要 {token_cost} 代幣，目前餘額 {user.tokens}）",
        )

    # 1. Gather card configs for this student
    configs_result = await db.execute(
        select(CardConfig).where(CardConfig.student_id == user.id)
    )
    configs = configs_result.scalars().all()

    if not configs:
        raise HTTPException(
            status_code=400,
            detail="尚未設定任何角色屬性，請先完成學習單元的屬性配置。",
        )

    # 2. Gather learning data
    records_result = await db.execute(
        select(LearningRecord, Unit)
        .join(Unit, LearningRecord.unit_id == Unit.id)
        .where(LearningRecord.student_id == user.id)
    )
    rows = records_result.all()

    # Build card_config dict — only include attributes from units the student
    # has actually completed (unit_1~5: quiz_score IS NOT NULL; unit_6: completion_rate > 0)
    active_unit_ids: set[int] = set()
    for record, unit in rows:
        if unit.code == "unit_6":
            if (record.completion_rate or 0) > 0:
                active_unit_ids.add(unit.id)
        else:
            if record.quiz_score is not None:
                active_unit_ids.add(unit.id)

    card_config: dict = {}
    for cfg in configs:
        if cfg.unit_id in active_unit_ids:
            card_config[cfg.attribute_type] = cfg.attribute_value

    unit_scores: dict = {}
    total_exp_sum = 0.0
    count = 0
    for record, unit in rows:
        unit_scores[unit.code] = {
            "homework": record.preview_score,
            "completion": record.completion_rate,
            "quiz": record.quiz_score,
        }
        if unit.code == "unit_6":
            exp = record.completion_rate or 0.0
        else:
            exp = (
                (record.preview_score or 0.0) * 0.2
                + (record.completion_rate or 0.0) * 0.4
                + (record.quiz_score or 0.0) * 0.4
            )
        total_exp_sum += exp
        if record.completion_rate is not None:
            count += 1

    learning_data = {
        "unit_scores": unit_scores,
        "overall_completion": round(total_exp_sum / 6, 1),
    }

    # 3. Determine level, rarity, and border from scoring rules
    from app.services.scoring import calculate_card_level, determine_border_style, roll_rarity

    level = calculate_card_level(total_exp_sum)
    rarity = roll_rarity(level)
    border = determine_border_style(rarity)

    card_config["border"] = border
    card_config["level"] = level        # 傳送完整 1~100 等級給 ai-worker
    card_config["rarity"] = rarity

    # expression / pose 未解鎖時不填預設值，由 ai-worker LLM 創意發揮

    # 3.5 Determine effective backend + resolve reference card for "keep" mode.
    # local backend ignores mode entirely (no image edit support); cloud + keep
    # uses the student's current display card as reference and overrides the
    # config's race/gender to the anchor's so metadata stays consistent with
    # the produced image (gpt-image-2 image edit always preserves face/race/
    # body/gender/hair from the reference).
    effective_backend = image_backend  # may differ from `image_backend` later
    reference_card_id: int | None = None
    reference_image_url: str | None = None

    if image_backend == "cloud" and mode_raw == "keep":
        anchor_result = await db.execute(
            select(Card).where(
                Card.student_id == user.id,
                Card.is_display == True,  # noqa: E712
                Card.status == "completed",
            )
        )
        anchor_card = anchor_result.scalar_one_or_none()
        if anchor_card is not None and anchor_card.image_url:
            reference_card_id = anchor_card.id
            reference_image_url = _resolve_reference_image_url(anchor_card.image_url)
            try:
                anchor_cfg = json.loads(anchor_card.config_snapshot or "{}")
            except (TypeError, ValueError):
                anchor_cfg = {}
            if isinstance(anchor_cfg, dict):
                for attr in ("race", "gender"):
                    anchor_val = anchor_cfg.get(attr)
                    chosen_val = card_config.get(attr)
                    if anchor_val and chosen_val != anchor_val:
                        logger.info(
                            "Student %s anchor override: %s %r -> %r (from card #%d)",
                            user.id, attr, chosen_val, anchor_val, anchor_card.id,
                        )
                        card_config[attr] = anchor_val
            logger.info(
                "Student %s using display card #%d as reference (resolved=%s)",
                user.id, anchor_card.id, reference_image_url,
            )
        else:
            # No display card yet — silently degrade to "fresh" generate. This
            # shouldn't happen via the dual-button UI (which hides "keep" when
            # there is no display card), but guard the API anyway.
            logger.info(
                "Student %s requested mode=keep but has no display card; "
                "falling back to fresh cloud generate", user.id,
            )

    # 4. Mark previous latest card as not latest (keep track to restore on failure)
    prev_latest_result = await db.execute(
        select(Card).where(Card.student_id == user.id, Card.is_latest == True)  # noqa: E712
    )
    prev_latest_cards = prev_latest_result.scalars().all()
    for prev_card in prev_latest_cards:
        prev_card.is_latest = False
        prev_card.is_display = False

    # Also clear any other stale is_display flags (guards against legacy data)
    stale_display_result = await db.execute(
        select(Card).where(Card.student_id == user.id, Card.is_display == True)  # noqa: E712
    )
    for stale_card in stale_display_result.scalars().all():
        stale_card.is_display = False

    # 5. Create new Card row and deduct tokens atomically
    config_snapshot = json.dumps(card_config, ensure_ascii=False)
    new_card = Card(
        student_id=user.id,
        config_snapshot=config_snapshot,
        status="pending",
        border_style=border,
        level_number=level,
        rarity=rarity,
        is_latest=True,
        is_display=True,
        backend_used=effective_backend,
        reference_card_id=reference_card_id,
    )
    db.add(new_card)

    if token_cost > 0:
        user.tokens -= token_cost
        db.add(TokenTransaction(
            student_id=user.id,
            amount=-token_cost,
            reason="生成新卡牌",
        ))
    else:
        # First generation is free — still record for history
        db.add(TokenTransaction(
            student_id=user.id,
            amount=0,
            reason="生成新卡牌（首次免費）",
        ))

    await db.commit()
    await db.refresh(new_card)

    # 6. Submit to ai-worker
    ai_worker = get_ai_worker_service()
    ollama_model = await get_system_setting(db, "ollama_model")
    # seed only meaningful for local SD; cloud backend ignores it
    seed_for_worker = requested_seed if effective_backend == "local" else None
    try:
        job_id = await ai_worker.submit_generation(
            card_id=new_card.id,
            student_id=user.student_id or "",
            student_nickname=user.nickname or user.name,
            card_config=card_config,
            learning_data=learning_data,
            seed=seed_for_worker,
            ollama_model_override=ollama_model,
            backend=effective_backend,
            reference_card_id=reference_card_id,
            reference_image_url=reference_image_url,
        )
        # Update card status to generating and persist job_id for polling
        new_card.status = "generating"
        new_card.job_id = job_id
        await db.commit()
    except Exception as e:
        logger.error("Failed to submit generation for card %d: %s", new_card.id, e)
        new_card.status = "failed"
        new_card.is_latest = False  # Don't let a failed card claim is_latest
        new_card.is_display = False  # Don't let a failed card hold is_display

        # Restore the previous latest card so the dashboard stays correct
        for prev_card in prev_latest_cards:
            if prev_card.status == "completed":
                prev_card.is_latest = True
                prev_card.is_display = True
                break

        # Refund tokens if the ai-worker submission failed
        if token_cost > 0:
            user.tokens += token_cost
            refund_txn = TokenTransaction(
                student_id=user.id,
                amount=token_cost,
                reason="AI服務失敗退款",
            )
            db.add(refund_txn)
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="無法連接 AI 生成服務，請稍後再試。",
        )

    return {
        "card_id": new_card.id,
        "job_id": job_id,
        "status": "generating",
        "tokens_spent": token_cost,
    }


@router.get("/generate-info")
async def generate_info(
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Settings for the student's generate UI.

    The progress page calls this on load to decide whether to show the
    "保持一致 / 重塑全新" dual button (cloud backend with a display card
    available) vs the seed input (local backend) vs a single button.

    Returns:
        image_backend: "local" | "cloud"
        display_card: { id, race, gender } | null
            Present when the student has a completed display card whose
            race/gender are known. The frontend uses these to warn about
            race/gender conflicts before submitting a "keep" request.
    """
    backend = await _read_image_backend(db)

    display_result = await db.execute(
        select(Card).where(
            Card.student_id == user.id,
            Card.is_display == True,  # noqa: E712
            Card.status == "completed",
        )
    )
    display_card = display_result.scalar_one_or_none()

    payload_card = None
    if display_card is not None:
        race = gender = None
        if display_card.config_snapshot:
            try:
                cfg = json.loads(display_card.config_snapshot)
                if isinstance(cfg, dict):
                    race = cfg.get("race")
                    gender = cfg.get("gender")
            except (TypeError, ValueError):
                pass
        payload_card = {
            "id": display_card.id,
            "race": race,
            "gender": gender,
        }

    return {
        "image_backend": backend,
        "display_card": payload_card,
    }


@router.get("/{card_id}/status")
async def card_status(
    card_id: int,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll card generation status.

    Also checks ai-worker for live job status if card is still generating.
    """
    result = await db.execute(
        select(Card).where(Card.id == card_id, Card.student_id == user.id)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="找不到此卡牌。")

    response = {
        "card_id": card.id,
        "status": card.status,
        "image_url": card.image_url,
        "thumbnail_url": card.thumbnail_url,
        "generated_at": card.generated_at.isoformat() if card.generated_at else None,
        "seconds_per_card": _SECONDS_PER_CARD.get(
            card.backend_used or "local", _SECONDS_PER_CARD["local"]
        ),
    }

    # If still generating, poll ai-worker for live queue position
    if card.status == "generating" and card.job_id:
        ai_worker = get_ai_worker_service()
        try:
            job_info = await ai_worker.check_job_status(card.job_id)
            if "position" in job_info:
                response["queue_position"] = job_info["position"]
            if "estimated_seconds" in job_info:
                response["estimated_seconds"] = job_info["estimated_seconds"]
        except Exception as exc:
            logger.warning("check_job_status failed for job %s: %s", card.job_id, exc)

    return response


@router.post("/{card_id}/set-display")
async def set_display_card(
    card_id: int,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a completed card as the hall display card for this student."""
    result = await db.execute(
        select(Card).where(Card.id == card_id, Card.student_id == user.id)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="找不到此卡牌。")
    if card.status != "completed":
        raise HTTPException(status_code=409, detail="只有生成完成的卡牌才能設為大廳展示。")

    # Clear is_display on all student's cards, then set this one
    all_cards_result = await db.execute(
        select(Card).where(Card.student_id == user.id, Card.is_display == True)  # noqa: E712
    )
    for c in all_cards_result.scalars().all():
        c.is_display = False

    card.is_display = True
    await db.commit()
    return {"success": True, "card_id": card_id}


@router.post("/{card_id}/hide")
async def hide_card(
    card_id: int,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a card from the student's gallery (admin still sees it)."""
    result = await db.execute(
        select(Card).where(Card.id == card_id, Card.student_id == user.id)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="找不到此卡牌。")

    was_display = card.is_display
    card.is_hidden = True
    card.is_display = False

    if was_display:
        # Auto-promote newest completed non-hidden card to display
        next_result = await db.execute(
            select(Card)
            .where(
                Card.student_id == user.id,
                Card.id != card_id,
                Card.status == "completed",
                Card.is_hidden == False,  # noqa: E712
            )
            .order_by(Card.created_at.desc())
            .limit(1)
        )
        next_card = next_result.scalar_one_or_none()
        if next_card is not None:
            next_card.is_display = True

    await db.commit()
    return {"success": True}
