"""
HTTP ingest webhook for n8n (cloud) -> PostgreSQL task queue.
Expects only: id, summary, source, description. No LLM; writes directly to PostgreSQL.

Why separate from MCP stdio server?
- MCP stdio uses stdin/stdout as the protocol transport. Web server logs on stdout can break it.
- Run this file as a separate process (uvicorn) alongside Cursor's MCP stdio server.

Run: uvicorn ingest_server:app --host 0.0.0.0 --port 8787
Then open http://localhost:8787/ or http://127.0.0.1:8787/ in the browser (not http://0.0.0.0:8787/ —
0.0.0.0 is a bind address, not a URL).

Endpoint:
- POST /ingest
  - Header: X-Ingest-Token: <secret>
  - Body: JSON with id (required), summary, source, description (all optional).
  - Writes to PostgreSQL (set DATABASE_URL).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import re

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

import database


def _sanitize_json_string(s: str) -> str:
    """Replace unescaped control characters so json.loads can parse (e.g. n8n sends real newlines in strings)."""
    def repl(m: re.Match) -> str:
        c = m.group(0)
        if c == "\n":
            return "\\n"
        if c == "\r":
            return "\\r"
        if c == "\t":
            return "\\t"
        return " "  # other control chars -> space
    return re.sub(r"[\x00-\x1f]", repl, s)


INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")

app = FastAPI(title="MCP Ingest Webhook", version="1.0.0")


class IngestTask(BaseModel):
    """Payload from n8n/HTTP: only id, summary, source, description accepted."""
    model_config = {"extra": "ignore"}

    id: str = Field(..., description="Unique identifier, e.g. SCRUM-1")
    summary: str | None = Field(default=None, description="Short title (Jira summary)")
    source: str | None = Field(default="jira")
    description: str | None = Field(default=None, description="Full text (Jira description)")

    @property
    def title(self) -> str:
        return (self.summary and self.summary.strip()) or self.id

    @property
    def instructions(self) -> str:
        return (
            (self.description and self.description.strip())
            or (self.summary and self.summary.strip())
            or "No description provided."
        )


def _require_token(header_token: str | None) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: set INGEST_TOKEN env var.",
        )
    if header_token != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="Bad X-Ingest-Token")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "ingest",
        "storage": "postgres",
    }


@app.post("/ingest")
async def ingest(
    body: Any = Body(...),
    x_ingest_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_ingest_token)

    # n8n sometimes sends:
    # - a JSON object (ideal)
    # - a string containing JSON (common if you pass LLM output directly)
    # - a wrapped object like { "data": {...} } or { "body": "<json string>" } depending on node settings
    raw = body
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Body was a string but not valid JSON: {e}") from e

    # Unwrap { "body": { ... } } or { "body": "<json string>" } (n8n sometimes sends this)
    if isinstance(raw, dict):
        body_val = raw.get("body") or raw.get("Body")
        if isinstance(body_val, dict):
            raw = body_val
        elif isinstance(body_val, str):
            try:
                raw = json.loads(body_val)
            except json.JSONDecodeError:
                try:
                    raw = json.loads(_sanitize_json_string(body_val))
                except Exception as e:
                    raise HTTPException(status_code=422, detail=f"body field was a string but not valid JSON: {e}") from e
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"body field was a string but not valid JSON: {e}") from e
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], (dict, str)):
        raw = raw["data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"data was a string but not valid JSON: {e}") from e

    # Accept array from n8n: use first element as the task
    if isinstance(raw, list):
        if not raw:
            raise HTTPException(status_code=422, detail="Expected a non-empty array or a single task object.")
        raw = raw[0]
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="Expected a JSON object or array with task (id, summary, source, description).")

    # Validate/normalize into our schema (gives good error messages).
    try:
        task = IngestTask.model_validate(raw)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid task payload. Expected id, summary, source, description. Error: {e}",
        ) from e

    task_id = task.id
    existing_status = await database.get_task_status(task_id)

    # Duplicate detection: if task exists and is pending or in_progress, return conflict
    if existing_status is not None:
        if existing_status in ("pending", "in_progress"):
            return {
                "ok": False,
                "summary": f"Task {task_id} already exists with status '{existing_status}'",
                "path": None,
                "error": {
                    "type": "duplicate",
                    "existing_status": existing_status,
                    "message": "Task already exists. Use update endpoint or complete/fail existing task first.",
                },
            }
        # Task was completed/failed, allow update (acts as retry/new version)
        updated_at = datetime.now(timezone.utc)
        await database.upsert_task(
            task_id,
            task.title,
            task.instructions,
            source=task.source or "jira",
            updated_at=updated_at,
            previous_status=existing_status,
        )
        return {"ok": True, "summary": f"Updated {task_id}", "path": None}
    else:
        await database.upsert_task(
            task_id,
            task.title,
            task.instructions,
            source=task.source or "jira",
        )
        return {"ok": True, "summary": f"Wrote {task_id}", "path": None}


