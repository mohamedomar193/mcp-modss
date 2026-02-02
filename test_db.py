"""
Quick test that the database connection works and task CRUD works.
Run from project folder: python test_db.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import database


async def main() -> None:
    print("Testing database connection...")
    if not os.getenv("DATABASE_URL", "").strip():
        print("FAIL: DATABASE_URL is not set (check .env)", file=sys.stderr)
        sys.exit(1)

    try:
        pool = await database.get_pool()
        print("OK: Connected to PostgreSQL")

        tasks = await database.list_tasks(limit=5, inbox_only=False)
        print(f"OK: list_tasks returned {len(tasks)} task(s)")

        # Create a test task
        test_id = "test-db-check"
        await database.enqueue_task(
            test_id,
            title="DB test task",
            instructions="Created by test_db.py to verify DB works.",
            source="test_db",
        )
        print("OK: enqueue_task succeeded")

        task = await database.get_task(test_id)
        if task:
            print(f"OK: get_task returned task: {task.get('title')}")
        else:
            print("FAIL: get_task returned None", file=sys.stderr)
            sys.exit(1)

        await database.close_pool()
        print("OK: Connection closed")
        print("\nDatabase test passed.")
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
