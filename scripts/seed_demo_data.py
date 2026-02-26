"""Seed demo students with learning records, card configs, and cards for guest mode.

Usage: uv run python scripts/seed_demo_data.py

Creates 3 demo students (DEMO001–DEMO003) with randomised learning records
across all 6 units, matching card configs, and placeholder cards.
Idempotent: deletes existing demo data before re-seeding.
"""

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import settings
from app.database import async_session, init_db
from app.models.card import Card
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.unit import Unit

# ── Demo student definitions ─────────────────────────────────────────────────

DEMO_STUDENTS = [
    {
        "student_id": "DEMO001",
        "name": "示範冒險者 A",
        "nickname": "HeroAlpha",
        "email": "demo-a@scholaverse.local",
        "tokens": 120,
    },
    {
        "student_id": "DEMO002",
        "name": "示範冒險者 B",
        "nickname": "MageBeta",
        "email": "demo-b@scholaverse.local",
        "tokens": 80,
    },
    {
        "student_id": "DEMO003",
        "name": "示範冒險者 C",
        "nickname": "KnightGamma",
        "email": "demo-c@scholaverse.local",
        "tokens": 50,
    },
]

# Attribute options per unit for card configs
UNIT_ATTRIBUTES: dict[str, dict[str, list[str]]] = {
    "unit_1": {
        "race": ["人類", "精靈", "矮人", "半身人", "龍裔"],
        "gender": ["男性", "女性"],
    },
    "unit_2": {
        "class": ["見習魔法師", "弓箭手", "鍛冶師", "聖騎士", "盜賊"],
        "body": ["標準", "壯碩", "纖細"],
    },
    "unit_3": {
        "equipment": ["學院制服", "皮革護甲", "鎖甲", "板甲", "暗影斗篷"],
    },
    "unit_4": {
        "weapon": ["木製法杖", "短弓", "戰斧", "聖劍", "匕首"],
    },
    "unit_5": {
        "background": ["圖書館", "森林邊境", "熔岩礦坑", "神殿廣場", "魔法塔頂"],
    },
    "unit_6": {
        "expression": ["自信微笑", "冷峻", "戰意昂揚"],
        "pose": ["站姿", "戰鬥姿態", "施法姿態"],
    },
}

# Placeholder images available (reuse existing ones)
PLACEHOLDER_IMAGES = [
    ("hero-copper-1.png", "copper", 1),
    ("hero-copper-2.png", "copper", 2),
    ("hero-silver-1.png", "silver", 3),
    ("hero-silver-2.png", "silver", 4),
    ("hero-gold-1.png", "gold", 5),
]


def _random_scores() -> dict[str, float | int]:
    """Generate random but realistic learning scores."""
    quiz = round(random.uniform(40, 100), 1)
    homework = round(random.uniform(50, 100), 1)
    completion = round(random.uniform(60, 100), 1)
    bonus = random.choice([0, 0, 0, 1, 2, 3, 5])
    return {
        "quiz_score": quiz,
        "homework_score": homework,
        "completion_rate": completion,
        "bonus_points": bonus,
    }


async def seed() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()

    async with async_session() as session:
        # ── 1. Fetch units ───────────────────────────────────────────────
        units_result = await session.execute(
            select(Unit).order_by(Unit.sort_order)
        )
        units = units_result.scalars().all()

        if not units:
            print("ERROR: No units found. Run seed_data.py first.")
            return

        unit_by_code = {u.code: u for u in units}
        print(f"Found {len(units)} units.")

        # ── 2. Clean up existing demo data ───────────────────────────────
        existing_result = await session.execute(
            select(Student).where(Student.student_id.like("DEMO%"))
        )
        existing_demos = existing_result.scalars().all()

        for demo in existing_demos:
            await session.execute(
                delete(Card).where(Card.student_id == demo.id)
            )
            await session.execute(
                delete(CardConfig).where(CardConfig.student_id == demo.id)
            )
            await session.execute(
                delete(LearningRecord).where(
                    LearningRecord.student_id == demo.id
                )
            )
            await session.execute(
                delete(Student).where(Student.id == demo.id)
            )
        if existing_demos:
            print(f"  Cleaned up {len(existing_demos)} existing demo student(s).")

        await session.flush()

        # ── 3. Create demo students + data ───────────────────────────────
        now = datetime.now(timezone.utc)

        for sdef in DEMO_STUDENTS:
            student = Student(
                student_id=sdef["student_id"],
                name=sdef["name"],
                nickname=sdef["nickname"],
                email=sdef["email"],
                role="student",
                tokens=sdef["tokens"],
            )
            session.add(student)
            await session.flush()  # get student.id
            print(f"\n  Created student: {sdef['student_id']} ({sdef['name']})")

            # How many units this student has completed (randomise 3-6)
            completed_units = random.randint(3, len(units))
            active_units = list(units)[:completed_units]

            card_config_snapshot: dict[str, str] = {}

            for unit in active_units:
                # Learning record
                scores = _random_scores()
                lr = LearningRecord(
                    student_id=student.id,
                    unit_id=unit.id,
                    preview_score=round(random.uniform(50, 95), 1),
                    **scores,
                )
                session.add(lr)

                # Card configs from this unit
                attrs = UNIT_ATTRIBUTES.get(unit.code, {})
                for attr_type, options in attrs.items():
                    chosen = random.choice(options)
                    cc = CardConfig(
                        student_id=student.id,
                        unit_id=unit.id,
                        attribute_type=attr_type,
                        attribute_value=chosen,
                        available_options=json.dumps(
                            options, ensure_ascii=False
                        ),
                    )
                    session.add(cc)
                    card_config_snapshot[attr_type] = chosen

                print(
                    f"    {unit.code}: quiz={scores['quiz_score']:.0f} "
                    f"hw={scores['homework_score']:.0f} "
                    f"comp={scores['completion_rate']:.0f}"
                )

            # Create a card for this student
            img_file, border, level = random.choice(PLACEHOLDER_IMAGES)
            image_url = f"/static/images/placeholder/{img_file}"
            card = Card(
                student_id=student.id,
                image_url=image_url,
                thumbnail_url=image_url,
                border_style=border,
                level_number=min(level, completed_units),
                is_latest=True,
                status="completed",
                config_snapshot=json.dumps(
                    card_config_snapshot, ensure_ascii=False
                ),
                generated_at=now,
                created_at=now,
            )
            session.add(card)
            print(f"    Card: Lv.{card.level_number} ({border})")

        await session.commit()

    print("\nDone. Demo data seeded for guest mode.")
    print("Set GUEST_MODE=true in .env to enable guest browsing.")


if __name__ == "__main__":
    asyncio.run(seed())
