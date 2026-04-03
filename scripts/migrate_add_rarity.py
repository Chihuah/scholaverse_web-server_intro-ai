"""Migration: 新增 cards.rarity 欄位"""
import asyncio
import aiosqlite

DB_PATH = "/var/www/app.scholaverse.cc/intro-ai/data/scholaverse.db"


async def migrate():
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if column already exists
        async with db.execute("PRAGMA table_info(cards)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]

        if "rarity" in cols:
            print("Column 'rarity' already exists, skipping.")
            return

        await db.execute("ALTER TABLE cards ADD COLUMN rarity TEXT")
        print("Column 'rarity' added.")

        # Backfill existing completed cards with 'N' as default
        await db.execute("UPDATE cards SET rarity = 'N' WHERE rarity IS NULL")
        await db.commit()
        print("Backfill: existing cards set to rarity='N'.")

        # Verify
        async with db.execute("SELECT COUNT(*) FROM cards WHERE rarity IS NOT NULL") as cur:
            count = (await cur.fetchone())[0]
        print(f"Done. {count} cards now have rarity set.")


asyncio.run(migrate())
