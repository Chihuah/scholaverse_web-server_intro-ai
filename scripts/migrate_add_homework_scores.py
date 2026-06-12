"""Migration: 新增 homework_scores 資料表（unit_6 作業加權平均的原始資料）"""
import asyncio
import aiosqlite

DB_PATH = "/var/www/app.scholaverse.cc/intro-ai/data/scholaverse.db"


async def migrate():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='homework_scores'"
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            print("Table 'homework_scores' already exists, skipping.")
            return

        await db.execute(
            """
            CREATE TABLE homework_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                assignment_name TEXT NOT NULL,
                score REAL NOT NULL,
                imported_at DATETIME,
                updated_at DATETIME,
                CONSTRAINT uq_homework_student_assignment
                    UNIQUE (student_id, assignment_name)
            )
            """
        )
        print("Table 'homework_scores' created.")

        # unit_6 完成度改由作業加權平均計算，舊的 TronClass 課程完成度數值
        # 已無意義且會造成錯誤解鎖，一律歸零（之後匯入成績單時會重算）。
        cursor = await db.execute(
            """
            UPDATE learning_records
            SET completion_rate = 0
            WHERE unit_id = (SELECT id FROM units WHERE code = 'unit_6')
              AND completion_rate IS NOT NULL
            """
        )
        print(f"Reset unit_6 completion_rate to 0 for {cursor.rowcount} records.")
        await db.commit()

        async with db.execute("PRAGMA table_info(homework_scores)") as cur:
            cols = [row[1] for row in await cur.fetchall()]
        print(f"Done. Columns: {cols}")


asyncio.run(migrate())
