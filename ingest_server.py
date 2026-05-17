"""
HTTP ingest webhook for n8n (cloud) -> PostgreSQL task queue.
Expects Jira task payloads. No LLM; writes directly to PostgreSQL.

Why separate from MCP stdio server?
- MCP stdio uses stdin/stdout as the protocol transport. Web server logs on stdout can break it.
- Run this file as a separate process (uvicorn) alongside Cursor's MCP stdio server.

Run: uvicorn ingest_server:app --host 0.0.0.0 --port 8787
Then open http://localhost:8787/ or http://127.0.0.1:8787/ in the browser (not http://0.0.0.0:8787/ —
0.0.0.0 is a bind address, not a URL).

Endpoint:
- POST /ingest
  - Header: X-Ingest-Token: <secret>
  - Body: JSON with id and generated instructions or description, plus optional Jira metadata.
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


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _list_of_objects(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [{"text": value}]
        return _list_of_objects(parsed)
    return []


def _json_object_from_string(value: str | None) -> dict[str, Any] | None:
    if not value or not value.strip().startswith("{"):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_jira_linked_test_cases(issue: dict[str, Any]) -> list[dict[str, Any]]:
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return []

    test_cases: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for link in _list_of_objects(fields.get("issuelinks")):
        link_type = link.get("type") if isinstance(link.get("type"), dict) else {}
        if link_type.get("name") != "AgileTest":
            continue

        for direction, issue_key in (("outward", "outwardIssue"), ("inward", "inwardIssue")):
            linked_issue = link.get(issue_key)
            if not isinstance(linked_issue, dict):
                continue
            linked_fields = linked_issue.get("fields") if isinstance(linked_issue.get("fields"), dict) else {}
            issue_type = linked_fields.get("issuetype") if isinstance(linked_fields.get("issuetype"), dict) else {}
            if issue_type.get("name") != "TestCase":
                continue

            key = linked_issue.get("key")
            if not isinstance(key, str) or key in seen_keys:
                continue
            seen_keys.add(key)

            status = linked_fields.get("status") if isinstance(linked_fields.get("status"), dict) else {}
            priority = linked_fields.get("priority") if isinstance(linked_fields.get("priority"), dict) else {}
            test_cases.append(
                {
                    "key": key,
                    "id": linked_issue.get("id"),
                    "self": linked_issue.get("self"),
                    "summary": linked_fields.get("summary"),
                    "status": status.get("name"),
                    "priority": priority.get("name"),
                    "issueType": issue_type.get("name"),
                    "linkId": link.get("id"),
                    "linkType": link_type.get("name"),
                    "linkDirection": direction,
                    "linkRelationship": link_type.get(direction),
                }
            )
    return test_cases


def _normalize_jira_issue_payload(raw: dict[str, Any]) -> dict[str, Any]:
    issue = raw.get("issue") if isinstance(raw.get("issue"), dict) else raw
    if not isinstance(issue, dict) or not isinstance(issue.get("fields"), dict):
        return raw

    fields = issue["fields"]
    normalized = dict(raw)
    normalized.setdefault("id", issue.get("key") or issue.get("id"))
    normalized.setdefault("summary", fields.get("summary"))
    normalized.setdefault("description", fields.get("description"))
    normalized.setdefault("acceptance_criteria", fields.get("customfield_10037"))

    issue_type = fields.get("issuetype")
    if isinstance(issue_type, dict):
        normalized.setdefault("issue_type", issue_type.get("name"))

    project = fields.get("project")
    meta = dict(normalized.get("meta") or {})
    if isinstance(project, dict):
        meta.setdefault("project_key", project.get("key"))
    normalized["meta"] = meta

    labels = fields.get("labels")
    if labels is not None:
        normalized.setdefault("labels", labels)

    components = fields.get("components")
    if isinstance(components, list):
        normalized.setdefault(
            "components",
            [component.get("name") for component in components if isinstance(component, dict) and component.get("name")],
        )

    test_cases = _extract_jira_linked_test_cases(issue)
    if test_cases:
        normalized.setdefault("test_cases", test_cases)

    return normalized


class IngestTask(BaseModel):
    """Payload from n8n/HTTP. Unknown fields are ignored by design."""
    model_config = {"extra": "ignore"}

    id: str = Field(..., description="Unique identifier, e.g. SCRUM-1")
    summary: str | None = Field(default=None, description="Short title (Jira summary)")
    title_field: str | None = Field(
        default=None,
        alias="title",
        description="Short title if sender does not use summary",
    )
    source: str | None = Field(default="jira")
    description: str | None = Field(default=None, description="Full text (Jira description)")
    instructions: str | None = Field(default=None, description="Generated task prompt from n8n")
    acceptance_criteria: list[str] | str | None = Field(default=None)
    file_hints: list[str] | str | None = Field(default=None)
    test_cases: list[dict[str, Any]] | dict[str, Any] | str | None = Field(
        default=None,
        alias="test_cases",
        description="Linked Jira TestCase data attached to the source story",
    )
    test_cases_camel: list[dict[str, Any]] | dict[str, Any] | str | None = Field(
        default=None,
        alias="testCases",
        description="Linked Jira TestCase data attached to the source story",
    )
    jira_test_cases: list[dict[str, Any]] | dict[str, Any] | str | None = Field(
        default=None,
        alias="jira_test_cases",
        description="Linked Jira TestCase data attached to the source story",
    )
    issue_type: str | None = Field(default=None)
    labels: list[str] | str | None = Field(default=None)
    components: list[str] | str | None = Field(default=None)
    meta: dict[str, Any] | None = Field(default=None)

    @property
    def title(self) -> str:
        return (
            (self.summary and self.summary.strip())
            or (self.title_field and self.title_field.strip())
            or self.id
        )

    @property
    def generated_payload(self) -> dict[str, Any]:
        return (
            _json_object_from_string(self.instructions)
            or _json_object_from_string(self.description)
            or {}
        )

    @property
    def task_instructions(self) -> str:
        payload = self.generated_payload
        parsed_instructions = payload.get("instructions")
        if isinstance(parsed_instructions, str) and parsed_instructions.strip():
            return parsed_instructions
        if payload:
            return ""
        if self.instructions and self.instructions.strip():
            return self.instructions
        if self.description and self.description.strip():
            return self.description
        return ""

    @property
    def criteria(self) -> list[str] | None:
        return _string_list(self.acceptance_criteria) or _string_list(
            self.generated_payload.get("acceptance_criteria")
        )

    @property
    def hints(self) -> list[str] | None:
        return _string_list(self.file_hints) or _string_list(self.generated_payload.get("file_hints"))

    @property
    def linked_test_cases(self) -> list[dict[str, Any]]:
        return (
            _list_of_objects(self.test_cases)
            or _list_of_objects(self.test_cases_camel)
            or _list_of_objects(self.jira_test_cases)
            or _list_of_objects(self.generated_payload.get("test_cases"))
            or _list_of_objects(self.generated_payload.get("testCases"))
            or _list_of_objects(self.generated_payload.get("jira_test_cases"))
        )

    @property
    def metadata(self) -> dict[str, Any] | None:
        meta = dict(self.meta or {})
        if self.issue_type:
            meta["issue_type"] = self.issue_type.strip()
        labels = _string_list(self.labels)
        components = _string_list(self.components)
        if labels is not None:
            meta["labels"] = labels
        if components is not None:
            meta["components"] = components
        test_cases = self.linked_test_cases
        if test_cases:
            meta["jira_test_cases"] = test_cases
        return meta or None


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
        raise HTTPException(
            status_code=422,
            detail="Expected a JSON object or array with task (id, summary/title, source, instructions/description).",
        )
    raw = _normalize_jira_issue_payload(raw)

    # Validate/normalize into our schema (gives good error messages).
    try:
        task = IngestTask.model_validate(raw)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid task payload. Expected id, summary/title, source, instructions/description, "
                "acceptance_criteria, file_hints, test_cases, issue_type, labels, components, "
                f"meta. Error: {e}"
            ),
        ) from e

    task_id = task.id
    if not task_id.strip():
        raise HTTPException(status_code=422, detail="Task id is required")
    if not task.task_instructions:
        raise HTTPException(status_code=422, detail="Task instructions or description is required")

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
            task.task_instructions,
            source=task.source or "jira",
            acceptance_criteria=task.criteria,
            file_hints=task.hints,
            meta=task.metadata,
            updated_at=updated_at,
            previous_status=existing_status,
        )
        return {"ok": True, "summary": f"Updated {task_id}", "path": None}
    else:
        await database.upsert_task(
            task_id,
            task.title,
            task.task_instructions,
            source=task.source or "jira",
            acceptance_criteria=task.criteria,
            file_hints=task.hints,
            meta=task.metadata,
        )
        return {"ok": True, "summary": f"Wrote {task_id}", "path": None}


