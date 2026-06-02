"""
MCP server for Cursor — HTTP only (e.g. deploy on EC2).

Exposes MCP tools for task management:
- Task queue operations (list, get, enqueue, complete, start, fail)
- Status tracking (pending, in_progress, completed, failed)
- PostgreSQL-backed task storage (set DATABASE_URL)
- Failed test reporting with automatic Jira Bug creation

Run: uvicorn mcp_server:app --host 0.0.0.0 --port 8000
Cursor connects via url (e.g. http://your-host:8000/mcp).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv()

import database

# Task status values (re-export for tool annotations)
TaskStatus = Literal["pending", "in_progress", "completed", "failed"]
DEFAULT_STATUS: TaskStatus = database.DEFAULT_STATUS

# Jira integration env vars
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://billqode.atlassian.net").rstrip("/")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

_VALID_SEVERITIES = {"critical", "high", "medium", "low", "trivial"}

_QA_GUIDELINES = """
## Billqode QA Playwright Guidelines

These guidelines apply to ALL test code written for the Billqode platform.
Follow them automatically without being asked.

---

### 1. Project Directory Structure (Page Object Model)

```
qa/
├── src/pages/adminPages/        ← Page Objects for Admin/Client dashboard
├── src/pages/customerPages/     ← Page Objects for Customer services
├── tests/adminDashBoard/        ← Spec files for Admin tests
└── tests/customerServices/      ← Spec files for Customer tests
```

Rules:
- Admin / Business Faced App scope  → page objects in src/pages/adminPages/,  specs in tests/adminDashBoard/
- Customer Faced App scope          → page objects in src/pages/customerPages/, specs in tests/customerServices/

---

### 2. Page Object Model (POM) Rules

- ALL locators must be declared in the constructor with descriptive property names
- ALL page interactions must be exposed as action methods (e.g. openFiltersDrawer, selectBranches)
- NEVER write assertions inside POM classes — assertions belong in .spec.ts files only
- ALWAYS wait for explicit state transitions inside action methods (drawer visible, spinner gone)

---

### 3. Locator Priority (Billqode-specific — supersedes any generic guide)

**TIER 1 — ALWAYS USE FIRST:**
- `page.getByRole()`  — buttons, links, inputs, checkboxes, headings, dialogs
- `page.getByText()`  — static text, labels, confirmation messages
- `page.getByLabel()` — form controls with a visible label

**TIER 2 — STABLE UNIQUE IDENTIFIERS (only when Tier 1 is not possible):**
- Stable CSS classes or custom data attributes (e.g. `data-testid`)

**TIER 3 — CHAKRA UI / OVERLAY COMPONENTS (special handling required):**
Visually hidden Chakra inputs CANNOT be clicked directly.

WRONG:
```typescript
await input.click();
await input.setChecked(true);
```

CORRECT:
```typescript
const checkboxLabel = popover.locator("label.chakra-checkbox__root").filter({ hasText: "Branch Name" });
const checkboxInput = checkboxLabel.locator('input[type="checkbox"]');
await checkboxLabel.click();                                    // click the label
await expect(checkboxInput).toBeChecked({ timeout: 5000 });    // verify on the input
```

**NEVER USE (brittle — will break on refactor):**
- CSS selectors: `locator('.class')`, `locator('#id')`, `locator('tag[attr]')`
- XPath: `locator('xpath=...')` or `locator('//...')`

---

### 4. Async / Timing Rules

- NEVER use `page.waitForTimeout()` — use explicit element waits instead
- ALWAYS verify state transitions before acting on child elements
- Wait for lazy-loaded lists before querying them:

```typescript
await popover.locator("label.chakra-checkbox__root").filter({ hasText: "Branch Name" })
             .waitFor({ state: "visible", timeout: 10000 });
```

- Multi-select state reset pattern (use before applying branch/filter selections):

```typescript
if (await selectAllInput.isChecked()) {
  await selectAllLabel.click();
  await expect(selectAllInput).not.toBeChecked({ timeout: 5000 });
} else {
  await selectAllLabel.click();
  await expect(selectAllInput).toBeChecked({ timeout: 5000 });
  await selectAllLabel.click();
  await expect(selectAllInput).not.toBeChecked({ timeout: 5000 });
}
```

---

### 5. Test File (.spec.ts) Standards

- Group tests with `test.describe()`
- Always read URLs and credentials from environment variables (`environments.env`), never hardcode
- Add `test.setTimeout(60000)` inside `beforeEach` for staging server tests
- Use `beforeEach` to login + navigate fresh for each test (no page state leakage between tests)
- Test names MUST include the Jira issue key: `test("BILQ-XXXXX: description", ...)`

---

### 6. Jira Component → Board Mapping

Both boards live under the same Jira project key **BILQ**.
The `Components` field identifies which app the issue belongs to:
- `business-facing-app` → Business Faced App board (Admin/Client dashboard tests)
- Customer-related components → Customer Faced App board (Customer services tests)
"""

# Disable DNS rebinding protection so any Host is accepted (e.g. behind nginx / proxy).
# json_response=True: server only requires Accept: application/json (not text/event-stream).
# stateless_http=True: no session ID required (each request is independent; works with Cursor).
mcp = FastMCP(
    "local-codegen",
    instructions=_QA_GUIDELINES.strip(),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    json_response=True,
    stateless_http=True,
)


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Jira helpers
# ---------------------------------------------------------------------------

def _jira_auth_headers() -> dict[str, str]:
    token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _map_severity_to_priority(severity: str) -> str:
    return {
        "critical": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "trivial": "Lowest",
    }.get(severity.lower(), "Medium")


async def _resolve_components(
    story_key: str | None,
    task_id: str | None,
) -> list[dict[str, str]]:
    """Return Jira component objects for the bug, inheriting from the linked story."""
    if story_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{JIRA_BASE_URL}/rest/api/3/issue/{story_key}",
                    headers=_jira_auth_headers(),
                    params={"fields": "components"},
                )
            if resp.status_code == 200:
                comps = resp.json().get("fields", {}).get("components", [])
                return [{"name": c["name"]} for c in comps if c.get("name")]
        except Exception:
            pass

    if task_id:
        task = await database.get_task(task_id)
        if task:
            comps = (task.get("meta") or {}).get("components", [])
            return [{"name": c} for c in comps if c]

    return []


def _build_adf_description(
    failure_reason: str | None,
    task_id: str | None,
    logs: str | None,
) -> dict[str, Any]:
    """Build a Jira ADF document for the bug description."""
    content: list[dict] = []

    if failure_reason:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Failure Reason"}],
        })
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": failure_reason}],
        })

    if task_id:
        content.append({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Task ID: ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": task_id},
            ],
        })

    if logs:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Logs"}],
        })
        content.append({
            "type": "codeBlock",
            "attrs": {},
            "content": [{"type": "text", "text": logs}],
        })

    if not content:
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": "Automated test failure — no details provided."}],
        })

    return {"version": 1, "type": "doc", "content": content}


async def _create_jira_issue(
    test_name: str,
    failure_reason: str | None,
    task_id: str | None,
    story_key: str | None,
    severity: str,
    logs: str | None,
) -> dict[str, str]:
    """Create a Jira Bug and return {"key": ..., "url": ...}. Raises RuntimeError on failure."""
    if not (JIRA_BASE_URL and JIRA_PROJECT_KEY and JIRA_EMAIL and JIRA_API_TOKEN):
        raise RuntimeError("Jira env vars (JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_EMAIL, JIRA_API_TOKEN) not configured.")

    components = await _resolve_components(story_key, task_id)

    payload: dict[str, Any] = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": f"[TEST FAILURE] {test_name}",
            "issuetype": {"name": "Bug"},
            "priority": {"name": _map_severity_to_priority(severity)},
            "labels": ["automated-test", "test-failure"],
            "description": _build_adf_description(failure_reason, task_id, logs),
        }
    }
    if components:
        payload["fields"]["components"] = components

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue",
            headers=_jira_auth_headers(),
            json=payload,
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Jira issue creation failed [{resp.status_code}]: {resp.text}")

    data = resp.json()
    key = data["key"]
    url = f"{JIRA_BASE_URL}/browse/{key}"
    return {"key": key, "url": url}


async def _attach_file_to_jira(issue_key: str, file_path: str) -> dict[str, Any]:
    """Attach a file to a Jira issue by server-side path. Returns info dict or raises on error."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Attachment not found: {file_path}")

    headers = {
        "Authorization": _jira_auth_headers()["Authorization"],
        "X-Atlassian-Token": "no-check",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        with p.open("rb") as f:
            resp = await client.post(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments",
                headers=headers,
                files={"file": (p.name, f, "application/octet-stream")},
            )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Attachment upload failed [{resp.status_code}]: {resp.text}")

    return {"file": p.name, "ok": True}


async def _attach_bytes_to_jira(
    issue_key: str,
    filename: str,
    content_b64: str,
) -> dict[str, Any]:
    """Attach a file to a Jira issue from base64-encoded content. Used for client-side files."""
    try:
        raw = base64.b64decode(content_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 content for {filename}: {exc}")

    headers = {
        "Authorization": _jira_auth_headers()["Authorization"],
        "X-Atlassian-Token": "no-check",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments",
            headers=headers,
            files={"file": (filename, raw, "application/octet-stream")},
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Attachment upload failed [{resp.status_code}]: {resp.text}")

    return {"file": filename, "ok": True}


async def _link_jira_issues(
    from_key: str,
    to_key: str,
    link_type: str = "relates to",
) -> None:
    """Link two Jira issues. Raises RuntimeError on failure."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{JIRA_BASE_URL}/rest/api/3/issueLink",
            headers=_jira_auth_headers(),
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": from_key},
                "outwardIssue": {"key": to_key},
            },
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Issue link failed [{resp.status_code}]: {resp.text}")


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

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


@mcp.tool(
    name="report_failed_test",
    description=(
        "Report a failed Playwright test. Stores the failure in PostgreSQL and automatically creates a Jira Bug "
        "with the failure details, priority based on severity, and labels=['automated-test','test-failure']. "
        "If story_key is provided (e.g. 'BILQ-42'), the bug is linked to that story via 'relates to' and inherits "
        "its Jira component (board assignment). Screenshots and videos are attached to the Jira issue. "
        "The DB record is always created even if Jira is unavailable."
    ),
)
async def report_failed_test(
    test_name: str,
    failure_reason: str,
    task_id: str | None = None,
    story_key: str | None = None,
    screenshot_path: str | None = None,
    video_path: str | None = None,
    logs: str | None = None,
    severity: str = "Medium",
) -> str:
    # Normalize severity
    severity = severity.strip().capitalize()
    if severity.lower() not in _VALID_SEVERITIES:
        severity = "Medium"

    # 1. Insert DB record first (survives Jira outages)
    try:
        failure_id = await database.insert_test_failure(
            test_name=test_name,
            failure_reason=failure_reason,
            task_id=task_id,
            severity=severity,
            screenshot_path=screenshot_path,
            video_path=video_path,
            logs=logs,
        )
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "summary": f"DB insert failed: {exc}",
            "failure_id": None,
            "jira_issue_key": None,
            "jira_issue_url": None,
            "linked_to_story": None,
            "attachments": [],
            "error": {"type": "db_error", "detail": str(exc)},
        }, ensure_ascii=False)

    jira_key: str | None = None
    jira_url: str | None = None
    linked_to_story: str | None = None
    attachments: list[dict] = []
    jira_error: str | None = None

    # 2. Create Jira bug
    jira_configured = all([JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_EMAIL, JIRA_API_TOKEN])
    if jira_configured:
        try:
            result = await _create_jira_issue(
                test_name=test_name,
                failure_reason=failure_reason,
                task_id=task_id,
                story_key=story_key,
                severity=severity,
                logs=logs,
            )
            jira_key = result["key"]
            jira_url = result["url"]
            await database.update_test_failure_jira(failure_id, jira_key, jira_url)
        except Exception as exc:
            jira_error = str(exc)

    # 3. Link to story
    if jira_key and story_key:
        try:
            await _link_jira_issues(jira_key, story_key)
            linked_to_story = story_key
        except Exception as exc:
            jira_error = (jira_error or "") + f" | Link failed: {exc}"

    # 4. Attach files
    if jira_key:
        for label, path in [("screenshot", screenshot_path), ("video", video_path)]:
            if not path:
                continue
            try:
                info = await _attach_file_to_jira(jira_key, path)
                attachments.append({"type": label, **info})
            except Exception as exc:
                attachments.append({"type": label, "file": path, "ok": False, "error": str(exc)})

    ok = jira_key is not None if jira_configured else True
    summary_parts = [f"Recorded failure #{failure_id} for '{test_name}'."]
    if jira_key:
        summary_parts.append(f"Jira bug: {jira_key}.")
    if linked_to_story:
        summary_parts.append(f"Linked to {linked_to_story}.")
    if jira_error and not jira_key:
        summary_parts.append(f"Jira error: {jira_error}")

    return json.dumps({
        "ok": ok,
        "summary": " ".join(summary_parts),
        "failure_id": failure_id,
        "jira_issue_key": jira_key,
        "jira_issue_url": jira_url,
        "linked_to_story": linked_to_story,
        "attachments": attachments,
        "error": {"detail": jira_error} if jira_error else None,
    }, ensure_ascii=False)


@mcp.tool(
    name="attach_to_jira_bug",
    description=(
        "Attach one or more local files to an existing Jira issue. "
        "Use this when report_failed_test could not attach files because they are on the local machine. "
        "Read each file as bytes, base64-encode it, and pass it in the 'files' list. "
        "Each entry must have: 'filename' (just the file name, no path) and 'content_b64' (base64-encoded file bytes). "
        "Example: attach a screenshot and video to BILQ-123 after a test failure."
    ),
)
async def attach_to_jira_bug(
    issue_key: str,
    files: list[dict[str, str]],
) -> str:
    """
    Attach local files to a Jira issue.

    files: list of {"filename": "test-failed-1.png", "content_b64": "<base64 bytes>"}
    """
    if not (JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN):
        return json.dumps({
            "ok": False,
            "summary": "Jira env vars not configured.",
            "attachments": [],
            "error": {"type": "config_error"},
        }, ensure_ascii=False)

    if not files:
        return json.dumps({
            "ok": False,
            "summary": "No files provided.",
            "attachments": [],
            "error": {"type": "no_files"},
        }, ensure_ascii=False)

    results: list[dict] = []
    for entry in files:
        filename = entry.get("filename", "attachment")
        content_b64 = entry.get("content_b64", "")
        try:
            info = await _attach_bytes_to_jira(issue_key, filename, content_b64)
            results.append(info)
        except Exception as exc:
            results.append({"file": filename, "ok": False, "error": str(exc)})

    succeeded = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    summary = f"Attached {len(succeeded)}/{len(files)} file(s) to {issue_key}."
    if failed:
        summary += f" {len(failed)} failed."

    return json.dumps({
        "ok": len(failed) == 0,
        "summary": summary,
        "attachments": results,
        "error": None if not failed else {"type": "partial_failure", "failed": failed},
    }, ensure_ascii=False)


# ASGI app for HTTP deployment. Run: uvicorn mcp_server:app --host 0.0.0.0 --port 8000
app = mcp.streamable_http_app()
