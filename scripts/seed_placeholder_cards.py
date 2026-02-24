"""Seed 10 placeholder cards under student 410510001 for UI testing.

Usage: uv run python scripts/seed_placeholder_cards.py

Creates (or reuses) the student, then inserts 10 cards using the
static placeholder images already in app/static/images/placeholder/.
The 5 "hero-*" images are marked is_latest=True so they appear in
the Hall of Heroes; the 5 "card-*" images are is_latest=False and
show only in the personal card gallery.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import settings
from app.database import async_session, init_db
from app.models.card import Card
from app.models.student import Student

STUDENT_ID = "410510001"

# 10 cards: (image filename, border_style, level_number, is_latest, config_snapshot)
CARDS = [
    # ── Hall of Heroes cards (is_latest=True) ──────────────────────────
    (
        "hero-copper-1.png", "copper", 1, True,
        {"race": "人類", "class": "見習魔法師", "equipment": "學院制服", "weapon": "木製法杖", "background": "圖書館"},
    ),
    (
        "hero-copper-2.png", "copper", 2, True,
        {"race": "精靈", "class": "弓箭手", "equipment": "皮革護甲", "weapon": "短弓", "background": "森林邊境"},
    ),
    (
        "hero-silver-1.png", "silver", 3, True,
        {"race": "矮人", "class": "鍛冶師", "equipment": "鎖甲", "weapon": "戰斧", "background": "熔岩礦坑"},
    ),
    (
        "hero-silver-2.png", "silver", 4, True,
        {"race": "人類", "class": "聖騎士", "equipment": "板甲", "weapon": "聖劍", "background": "神殿廣場"},
    ),
    (
        "hero-gold-1.png", "gold", 5, True,
        {"race": "龍裔", "class": "大法師", "equipment": "星紋法袍", "weapon": "元素法杖", "background": "魔法塔頂"},
    ),
    # ── Gallery-only cards (is_latest=False) ───────────────────────────
    (
        "card-copper-1.png", "copper", 1, False,
        {"race": "人類", "class": "新手冒險者", "equipment": "布衣", "weapon": "短劍", "background": "新手村"},
    ),
    (
        "card-silver-1.png", "silver", 2, False,
        {"race": "半身人", "class": "盜賊", "equipment": "暗影斗篷", "weapon": "匕首", "background": "城市小巷"},
    ),
    (
        "card-silver-2.png", "silver", 3, False,
        {"race": "精靈", "class": "德魯伊", "equipment": "藤蔓護甲", "weapon": "月牙鐮刀", "background": "古老森林"},
    ),
    (
        "card-gold-1.png", "gold", 4, False,
        {"race": "人類", "class": "召喚師", "equipment": "靈能戰甲", "weapon": "召喚書", "background": "次元裂縫"},
    ),
    (
        "card-dashboard.png", "gold", 5, False,
        {"race": "神裔", "class": "命運使者", "equipment": "時空披風", "weapon": "命運之鑰", "background": "時空隙縫"},
    ),
]


async def seed() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()

    async with async_session() as session:
        # ── 1. Find or create student ──────────────────────────────────
        result = await session.execute(
            select(Student).where(Student.student_id == STUDENT_ID)
        )
        student = result.scalar_one_or_none()

        if student is None:
            student = Student(
                student_id=STUDENT_ID,
                name="測試學生",
                email=f"__unbound__{STUDENT_ID}@placeholder",
                role="student",
                tokens=100,
            )
            session.add(student)
            await session.flush()  # get student.id
            print(f"  Created student: {STUDENT_ID} (id={student.id})")
        else:
            print(f"  Found student:  {STUDENT_ID} (id={student.id}, email={student.email})")

        # ── 2. Remove existing cards for this student ──────────────────
        deleted = await session.execute(
            delete(Card).where(Card.student_id == student.id)
        )
        print(f"  Deleted {deleted.rowcount} existing card(s).")

        # ── 3. Insert 10 placeholder cards ─────────────────────────────
        now = datetime.now(timezone.utc)
        for i, (filename, border, level, is_latest, snap) in enumerate(CARDS):
            image_url = f"/static/images/placeholder/{filename}"
            card = Card(
                student_id=student.id,
                image_url=image_url,
                thumbnail_url=image_url,
                border_style=border,
                level_number=level,
                is_latest=is_latest,
                status="completed",
                config_snapshot=json.dumps(snap, ensure_ascii=False),
                generated_at=now,
                created_at=now,
            )
            session.add(card)
            mark = "★ Hall" if is_latest else "  Gallery"
            print(f"  [{mark}] Lv.{level} {border:6s}  {filename}")

        await session.commit()

    print("\nDone. 10 placeholder cards seeded for student", STUDENT_ID)
    print("  Hall of Heroes : 5 cards (hero-*.png, is_latest=True)")
    print("  Gallery only   : 5 cards (card-*.png, is_latest=False)")


if __name__ == "__main__":
    asyncio.run(seed())
