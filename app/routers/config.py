"""Card configuration API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.unit import Unit
from app.services.scoring import get_available_options

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdateRequest(BaseModel):
    attribute_type: str
    attribute_value: str


class ConfigUpdateResponse(BaseModel):
    id: int
    unit_code: str
    attribute_type: str
    attribute_value: str
    tokens_spent: int


@router.get("/{unit_code}/options")
async def get_config_options(
    unit_code: str,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return available attribute options for a unit based on user's scores."""
    # Find the unit
    result = await db.execute(select(Unit).where(Unit.code == unit_code))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Get user's learning record for this unit
    lr_result = await db.execute(
        select(LearningRecord).where(
            LearningRecord.student_id == user.id,
            LearningRecord.unit_id == unit.id,
        )
    )
    record = lr_result.scalar_one_or_none()

    if record is None:
        return {"unit_code": unit_code, "options": {}, "message": "No learning record found"}

    # Get character class for weapon affinity (unit_4)
    character_class = None
    if unit_code == "unit_4":
        class_config = await db.execute(
            select(CardConfig).where(
                CardConfig.student_id == user.id,
                CardConfig.attribute_type == "class",
            )
        )
        cc = class_config.scalar_one_or_none()
        if cc:
            character_class = cc.attribute_value

    options = await get_available_options(
        unit_code=unit_code,
        quiz_score=record.quiz_score or 0,
        completion_rate=record.completion_rate,
        character_class=character_class,
        db=db,
    )

    return {"unit_code": unit_code, "options": options}


@router.put("/{unit_code}", response_model=ConfigUpdateResponse)
async def update_config(
    unit_code: str,
    body: ConfigUpdateRequest,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user's card config for a given unit."""
    # Find the unit
    result = await db.execute(select(Unit).where(Unit.code == unit_code))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Check if config already exists
    existing_result = await db.execute(
        select(CardConfig).where(
            CardConfig.student_id == user.id,
            CardConfig.unit_id == unit.id,
            CardConfig.attribute_type == body.attribute_type,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # Changing an attribute selection is free — tokens are spent at generation time
        existing.attribute_value = body.attribute_value
        await db.commit()
        await db.refresh(existing)

        return ConfigUpdateResponse(
            id=existing.id,
            unit_code=unit_code,
            attribute_type=existing.attribute_type,
            attribute_value=existing.attribute_value,
            tokens_spent=0,
        )
    else:
        # New config — free
        config = CardConfig(
            student_id=user.id,
            unit_id=unit.id,
            attribute_type=body.attribute_type,
            attribute_value=body.attribute_value,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

        return ConfigUpdateResponse(
            id=config.id,
            unit_code=unit_code,
            attribute_type=config.attribute_type,
            attribute_value=config.attribute_value,
            tokens_spent=0,
        )
