"""
PostgreSQL-backed task storage for the MCP task queue.

Uses DATABASE_URL env var. Exposes async functions for task CRUD
and status updates. One shared connection pool per process.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

import asyncpg

TaskStatus = Literal["pending", "in_progress", "completed", "failed"]
DEFAULT_STATUS: TaskStatus = "pending"

_DB_POOL: asyncpg.Pool | None = None


def _json_or_none(value: Any) -> str | None:
    """Serialize JSON values while preserving empty lists/dicts when provided."""
    return json.dumps(value) if value is not None else None


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to a PostgreSQL connection string, e.g. "
            "postgresql://user:pass@localhost:5432/mcp_tasks"
        )
    return url


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use."""
    global _DB_POOL
    if _DB_POOL is None:
        _DB_POOL = await asyncpg.create_pool(
            get_database_url(),
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    return _DB_POOL


async def close_pool() -> None:
    """Close the shared pool. Call on shutdown if needed."""
    global _DB_POOL
    if _DB_POOL is not None:
        await _DB_POOL.close()
        _DB_POOL = None


def _row_to_task(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a DB row to a task dict (same shape as file-based JSON)."""
    return {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "instructions": row["instructions"],
        "acceptance_criteria": _json_value(row["acceptance_criteria"], []),
        "file_hints": _json_value(row["file_hints"], []),
        "meta": _json_value(row["meta"], None),
        "status": row["status"] or DEFAULT_STATUS,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "failed_at": row["failed_at"].isoformat() if row["failed_at"] else None,
        "completion_note": row["completion_note"],
        "failure_reason": row["failure_reason"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "previous_status": row["previous_status"],
    }


async def list_tasks(
    limit: int = 20,
    status: TaskStatus | None = None,
    inbox_only: bool = True,
) -> list[dict[str, Any]]:
    """
    List tasks. If inbox_only=True (default), only pending and in_progress.
    If status is set, filter by that status. Returns list of task dicts for summary view.
    """
    pool = await get_pool()
    if status is not None:
        query = """
            SELECT id, source, title, status, created_at
            FROM tasks
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        rows = await pool.fetch(query, status, limit)
    elif inbox_only:
        query = """
            SELECT id, source, title, status, created_at
            FROM tasks
            WHERE status IN ('pending', 'in_progress')
            ORDER BY created_at DESC
            LIMIT $1
        """
        rows = await pool.fetch(query, limit)
    else:
        query = """
            SELECT id, source, title, status, created_at
            FROM tasks
            ORDER BY created_at DESC
            LIMIT $1
        """
        rows = await pool.fetch(query, limit)

    return [
        {
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "status": r["status"] or DEFAULT_STATUS,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "path": None,
        }
        for r in rows
    ]


async def get_task(task_id: str) -> dict[str, Any] | None:
    """Get full task by id. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, source, title, instructions, acceptance_criteria, file_hints, meta,
               status, created_at, started_at, completed_at, failed_at,
               completion_note, failure_reason, updated_at, previous_status
        FROM tasks
        WHERE id = $1
        """,
        task_id,
    )
    if row is None:
        return None
    return _row_to_task(row)


async def enqueue_task(
    task_id: str,
    title: str,
    instructions: str,
    source: str = "manual",
    extra: dict[str, Any] | None = None,
) -> None:
    """Insert a new task (status pending)."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    acceptance_criteria = (extra or {}).get("acceptance_criteria")
    file_hints = (extra or {}).get("file_hints")
    meta = (extra or {}).get("meta")
    if acceptance_criteria is not None and not isinstance(acceptance_criteria, list):
        acceptance_criteria = []
    if file_hints is not None and not isinstance(file_hints, list):
        file_hints = []

    await pool.execute(
        """
        INSERT INTO tasks (id, source, title, instructions, acceptance_criteria, file_hints, meta, status, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        task_id,
        source,
        title or "",
        instructions or "",
        _json_or_none(acceptance_criteria),
        _json_or_none(file_hints),
        _json_or_none(meta),
        DEFAULT_STATUS,
        now,
    )


async def update_task_status(
    task_id: str,
    status: TaskStatus,
    *,
    completion_note: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Update task status and set timestamp fields. Returns updated task or None if not found."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    if status == "in_progress":
        await pool.execute(
            "UPDATE tasks SET status = $1, started_at = $2 WHERE id = $3",
            status,
            now,
            task_id,
        )
    elif status == "completed":
        await pool.execute(
            "UPDATE tasks SET status = $1, completed_at = $2, completion_note = $3 WHERE id = $4",
            status,
            now,
            completion_note,
            task_id,
        )
    elif status == "failed":
        await pool.execute(
            "UPDATE tasks SET status = $1, failed_at = $2, failure_reason = $3 WHERE id = $4",
            status,
            now,
            failure_reason,
            task_id,
        )
    else:
        await pool.execute("UPDATE tasks SET status = $1 WHERE id = $2", status, task_id)

    return await get_task(task_id)


async def task_exists(task_id: str) -> bool:
    """Return True if a task with this id exists."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT 1 FROM tasks WHERE id = $1", task_id)
    return row is not None


async def get_task_status(task_id: str) -> TaskStatus | None:
    """Return current status or None if task not found."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT status FROM tasks WHERE id = $1", task_id)
    if row is None:
        return None
    s = row["status"]
    if s in ("pending", "in_progress", "completed", "failed"):
        return s  # type: ignore
    return DEFAULT_STATUS


async def upsert_task(
    task_id: str,
    title: str | None,
    instructions: str,
    source: str = "jira",
    acceptance_criteria: list[str] | None = None,
    file_hints: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    *,
    updated_at: datetime | None = None,
    previous_status: str | None = None,
) -> None:
    """
    Insert or update a task (for ingest). Used when we allow overwrite (e.g. after completed/failed).
    Sets status to pending, created_at to now if insert; sets updated_at/previous_status if update.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    await pool.execute(
        """
        INSERT INTO tasks (id, source, title, instructions, acceptance_criteria, file_hints, meta, status, created_at, updated_at, previous_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (id) DO UPDATE SET
            source = EXCLUDED.source,
            title = EXCLUDED.title,
            instructions = EXCLUDED.instructions,
            acceptance_criteria = EXCLUDED.acceptance_criteria,
            file_hints = EXCLUDED.file_hints,
            meta = EXCLUDED.meta,
            status = 'pending',
            started_at = NULL,
            completed_at = NULL,
            failed_at = NULL,
            completion_note = NULL,
            failure_reason = NULL,
            updated_at = EXCLUDED.updated_at,
            previous_status = EXCLUDED.previous_status
        """,
        task_id,
        source or "jira",
        title or "",
        instructions or "",
        _json_or_none(acceptance_criteria),
        _json_or_none(file_hints),
        _json_or_none(meta),
        DEFAULT_STATUS,
        now,
        updated_at,
        previous_status,
    )
