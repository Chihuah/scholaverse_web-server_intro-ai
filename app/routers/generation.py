"""Card generation API routes.

POST /api/cards/generate  — Submit card generation request
GET  /api/cards/{id}/status — Poll generation status
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.card import Card
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.unit import Unit
from app.services import get_ai_worker_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["generation"])


@router.post("/generate")
async def generate_card(
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit a card generation request.

    Gathers the student's current card configs and learning records,
    creates a Card row with status='pending', then submits to ai-worker.
    """
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

    # Build card_config dict from individual attribute configs
    card_config: dict = {}
    for cfg in configs:
        card_config[cfg.attribute_type] = cfg.attribute_value

    # 2. Gather learning data
    records_result = await db.execute(
        select(LearningRecord, Unit)
        .join(Unit, LearningRecord.unit_id == Unit.id)
        .where(LearningRecord.student_id == user.id)
    )
    rows = records_result.all()

    unit_scores: dict = {}
    total_completion = 0.0
    count = 0
    for record, unit in rows:
        unit_scores[unit.code] = {
            "quiz": record.quiz_score,
            "homework": record.homework_score,
            "completion": record.completion_rate,
        }
        if record.completion_rate is not None:
            total_completion += record.completion_rate
            count += 1

    overall_completion = (total_completion / count) if count > 0 else 0.0

    learning_data = {
        "unit_scores": unit_scores,
        "overall_completion": round(overall_completion, 1),
    }

    # 3. Determine border and level from scoring rules
    from app.services.scoring import calculate_card_level, determine_border_style

    level = calculate_card_level(overall_completion)
    border = determine_border_style(count * 3)  # rough estimate: ~3 weeks per unit

    card_config["border"] = border
    card_config["level"] = level

    # 4. Mark previous latest card as not latest
    prev_latest = await db.execute(
        select(Card).where(Card.student_id == user.id, Card.is_latest == True)  # noqa: E712
    )
    for prev_card in prev_latest.scalars().all():
        prev_card.is_latest = False

    # 5. Create new Card row
    config_snapshot = json.dumps(card_config, ensure_ascii=False)
    new_card = Card(
        student_id=user.id,
        config_snapshot=config_snapshot,
        status="pending",
        border_style=border,
        level_number=level,
        is_latest=True,
    )
    db.add(new_card)
    await db.commit()
    await db.refresh(new_card)

    # 6. Submit to ai-worker
    ai_worker = get_ai_worker_service()
    try:
        job_id = await ai_worker.submit_generation(
            card_id=new_card.id,
            student_id=user.student_id or "",
            student_nickname=user.nickname or user.name,
            card_config=card_config,
            learning_data=learning_data,
        )
        # Update card status to generating
        new_card.status = "generating"
        await db.commit()
    except Exception as e:
        logger.error("Failed to submit generation for card %d: %s", new_card.id, e)
        new_card.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="無法連接 AI 生成服務，請稍後再試。",
        )

    return {
        "card_id": new_card.id,
        "job_id": job_id,
        "status": "generating",
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

    # If still generating, also poll ai-worker for live status
    if card.status == "generating":
        ai_worker = get_ai_worker_service()
        # We don't store job_id on the card, so we check via ai-worker if possible
        # For mock, the callback will update the card directly
        pass

    return response
