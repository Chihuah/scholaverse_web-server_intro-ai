"""Card generation API routes.

POST /api/cards/generate  — Submit card generation request
GET  /api/cards/{id}/status — Poll generation status
"""

import json
import math
import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

logger = logging.getLogger(__name__)

class GenerateCardRequest(BaseModel):
    seed: int = -1

    @field_validator("seed")
    @classmethod
    def validate_seed(cls, v: int) -> int:
        if v != -1 and v < 0:
            raise ValueError("種子數必須為 -1（隨機）或正整數")
        return v

router = APIRouter(prefix="/api/cards", tags=["generation"])


@router.post("/generate")
async def generate_card(
    body: Optional[GenerateCardRequest] = None,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit a card generation request.

    Gathers the student's current card configs and learning records,
    creates a Card row with status='pending', then submits to ai-worker.
    """
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

    # Resolve requested seed: -1 or None means random (pass None to ai-worker)
    requested_seed: Optional[int] = None
    if body is not None and body.seed != -1:
        requested_seed = body.seed

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
    try:
        job_id = await ai_worker.submit_generation(
            card_id=new_card.id,
            student_id=user.student_id or "",
            student_nickname=user.nickname or user.name,
            card_config=card_config,
            learning_data=learning_data,
            seed=requested_seed,
            ollama_model_override=ollama_model,
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


@router.get("/display-seed")
async def get_display_card_seed(
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the seed of the student's current display card."""
    result = await db.execute(
        select(Card).where(
            Card.student_id == user.id,
            Card.is_display == True,  # noqa: E712
            Card.status == "completed",
        ).limit(1)
    )
    card = result.scalar_one_or_none()
    if card is None:
        return {"seed": None, "card_id": None}
    return {"seed": card.seed, "card_id": card.id}
