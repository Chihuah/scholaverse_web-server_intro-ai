"""Migration: 更新 unit_6 表情/姿勢規則字眼

- expression A: passionate（激昂）→ triumphant（意氣風發）
- expression D: weary（疲憊）→ contemplative（沉思）
- pose S: charging（衝鋒陷陣）→ victorious（凱旋之姿）
- pose C: crouching（蹲坐）→ kneeling（單膝跪地）
- pose D: crouching（蹲坐）→ sitting（席地而坐），修正 C/D 重複
"""
import asyncio
import json

import aiosqlite

DB_PATH = "/var/www/app.scholaverse.cc/intro-ai/data/scholaverse.db"

# (attribute_type, tier) → (options, labels)
RULE_UPDATES: dict[tuple[str, str], tuple[list[str], dict[str, str]]] = {
    ("expression", "A"): (["triumphant"], {"triumphant": "意氣風發"}),
    ("expression", "D"): (["contemplative"], {"contemplative": "沉思"}),
    ("pose", "S"): (["victorious"], {"victorious": "凱旋之姿"}),
    ("pose", "C"): (["kneeling"], {"kneeling": "單膝跪地"}),
    ("pose", "D"): (["sitting"], {"sitting": "席地而坐"}),
}


async def migrate():
    async with aiosqlite.connect(DB_PATH) as db:
        total = 0
        for (attr_type, tier), (options, labels) in RULE_UPDATES.items():
            cursor = await db.execute(
                """
                UPDATE attribute_rules
                SET options = ?, labels = ?
                WHERE unit_code = 'unit_6' AND attribute_type = ? AND tier = ?
                """,
                (
                    json.dumps(options, ensure_ascii=False),
                    json.dumps(labels, ensure_ascii=False),
                    attr_type,
                    tier,
                ),
            )
            print(f"{attr_type}/{tier} → {options} ({cursor.rowcount} row)")
            total += cursor.rowcount
        await db.commit()
        print(f"Done. {total} rules updated.")

        async with db.execute(
            "SELECT attribute_type, tier, options, labels FROM attribute_rules "
            "WHERE unit_code = 'unit_6' ORDER BY attribute_type, tier"
        ) as cur:
            for row in await cur.fetchall():
                print(row)


asyncio.run(migrate())
