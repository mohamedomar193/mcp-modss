"""
Create the tasks table in PostgreSQL. Run once after setting DATABASE_URL.

Usage:
    Copy env.example to .env, set DATABASE_URL, then: python init_db.py
    Or: set DATABASE_URL=postgresql://user:pass@localhost:5432/mcp_tasks
        python init_db.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
import asyncpg

load_dotenv()


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    source TEXT,
    title TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    acceptance_criteria JSONB,
    file_hints JSONB,
    meta JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    completion_note TEXT,
    failure_reason TEXT,
    updated_at TIMESTAMPTZ,
    previous_status TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC);
"""


async def main() -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(TABLE_SQL)
        print("Table 'tasks' created (or already exists).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
