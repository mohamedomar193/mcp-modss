"""
MCP server for Cursor — HTTP only (e.g. deploy on EC2).

Exposes MCP tools for task management:
- Task queue operations (list, get, enqueue, complete, start, fail)
- Status tracking (pending, in_progress, completed, failed)
- PostgreSQL-backed task storage (set DATABASE_URL)

Run: uvicorn mcp_server:app --host 0.0.0.0 --port 8000
Cursor connects via url (e.g. http://your-host:8000/mcp).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv()

import database
from llm import enhance_task

# Task status values (re-export for tool annotations)
TaskStatus = Literal["pending", "in_progress", "completed", "failed"]
DEFAULT_STATUS: TaskStatus = database.DEFAULT_STATUS

# Disable DNS rebinding protection so any Host is accepted (e.g. behind nginx / proxy).
# json_response=True: server only requires Accept: application/json (not text/event-stream).
# stateless_http=True: no session ID required (each request is independent; works with Cursor).
mcp = FastMCP(
    "local-codegen",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    json_response=True,
    stateless_http=True,
)


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@mcp.tool(
    name="list_tasks",
    description=(
        "List tasks created by external systems (e.g., n8n/Jira) from the local tasks inbox. "
        "Use this to see what work is queued for the agent to apply. Returns JSON text with stable keys. "
        "Can filter by status: pending, in_progress, completed, failed."
    ),
)
async def list_tasks(
    limit: int = 20,
    status: TaskStatus | None = None,
) -> str:
    inbox_only = status is None
    items = await database.list_tasks(limit=max(0, limit), status=status, inbox_only=inbox_only)
    return json.dumps({"ok": True, "summary": f"Found {len(items)} inbox task(s).", "tasks": items, "error": None})


@mcp.tool(
    name="get_task",
    description=(
        "Get a single queued task by id (or filename stem) from the local tasks inbox. "
        "Returns the full structured task JSON as text so the agent can generate a patch from it."
    ),
)
async def get_task(task_id: str) -> str:
    task = await database.get_task(task_id)
    if task is None:
        return json.dumps(
            {"ok": False, "summary": f"Task not found: {task_id}", "task": None, "error": {"type": "not_found"}},
            ensure_ascii=False,
        )

    # Enhance task with LLM before returning to Cursor (falls back to original on failure)
    enhanced = await enhance_task(
        task.get("title"), task.get("instructions", ""),
        task.get("acceptance_criteria"), task.get("file_hints"),
    )
    task["instructions"] = enhanced["instructions"]
    task["acceptance_criteria"] = enhanced["acceptance_criteria"]
    task["file_hints"] = enhanced["file_hints"]

    return json.dumps({"ok": True, "summary": f"Loaded task {task['id']}.", "task": task, "error": None}, ensure_ascii=False)


@mcp.tool(
    name="enqueue_task",
    description=(
        "Create a new queued task in the local tasks inbox. "
        "This is useful if you want to simulate n8n/Jira ingestion or you have another local process creating tasks."
    ),
)
async def enqueue_task(
    title: str,
    instructions: str,
    *,
    source: str = "manual",
    task: dict[str, Any] | None = None,
) -> str:
    task_id = (task or {}).get("id") or f"{_now_id()}-{source}".replace(" ", "-")
    extra = dict(task) if task else None
    if extra:
        extra.setdefault("id", task_id)
        extra.setdefault("title", title)
        extra.setdefault("source", source)
    await database.enqueue_task(task_id, title, instructions, source=source, extra=extra)
    return json.dumps(
        {"ok": True, "summary": f"Enqueued task {task_id}.", "task_id": task_id, "path": None, "error": None},
        ensure_ascii=False,
    )


@mcp.tool(
    name="complete_task",
    description=(
        "Mark a task as completed by moving it from tasks/inbox to tasks/done. "
        "Call this after applying and validating the changes described by the task."
    ),
)
async def complete_task(task_id: str, *, note: str | None = None) -> str:
    task = await database.update_task_status(task_id, "completed", completion_note=note)
    if task is None:
        return json.dumps(
            {"ok": False, "summary": f"Task not found: {task_id}", "moved": False, "error": {"type": "not_found"}},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "summary": f"Completed task {task.get('id') or task_id}.", "moved": True, "path": None, "error": None},
        ensure_ascii=False,
    )


@mcp.tool(
    name="start_task",
    description=(
        "Mark a task as in_progress. Call this when you begin working on a task. "
        "This helps track which tasks are currently being processed."
    ),
)
async def start_task(task_id: str) -> str:
    task = await database.update_task_status(task_id, "in_progress")
    if task is None:
        return json.dumps(
            {"ok": False, "summary": f"Task not found: {task_id}", "error": {"type": "not_found"}},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "summary": f"Started task {task.get('id') or task_id}.", "task": task, "error": None},
        ensure_ascii=False,
    )


@mcp.tool(
    name="fail_task",
    description=(
        "Mark a task as failed. Call this when a task cannot be completed or encounters an error. "
        "The task remains in the inbox for review or retry."
    ),
)
async def fail_task(task_id: str, *, reason: str | None = None) -> str:
    task = await database.update_task_status(task_id, "failed", failure_reason=reason)
    if task is None:
        return json.dumps(
            {"ok": False, "summary": f"Task not found: {task_id}", "error": {"type": "not_found"}},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "summary": f"Marked task {task.get('id') or task_id} as failed.", "task": task, "error": None},
        ensure_ascii=False,
    )


# ASGI app for HTTP deployment. Run: uvicorn mcp_server:app --host 0.0.0.0 --port 8000
app = mcp.streamable_http_app()
