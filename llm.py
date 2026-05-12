"""
LLM-powered task enhancement using OpenAI (GPT-5 mini) with RAG.

Instead of sending the full architecture handbook with every request,
we send a small Core Policy (always) plus only the documentation
sections relevant to each Jira ticket (keyword-matched from rag_docs).

This reduces token usage by ~30-50% while improving precision.
Gracefully falls back to original data if OPENAI_API_KEY is not set
or the API call fails.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from openai import AsyncOpenAI

from rag_docs import SECTIONS

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAX_SECTIONS = 5  # Cap retrieved sections to control prompt size
TicketClassification = Literal[
    "new_crud_module",
    "existing_feature_enhancement",
    "reporting_filter_change",
    "frontend_ui_change",
    "backend_api_change",
    "bug_fix",
    "unknown",
]

# ── Core Policy (always included, ~20 lines) ──────────────────────────
CORE_POLICY = """\
You are the BillQode Backend AI assistant integrated with Jira via MCP.

Project: Laravel 11 (PHP 8.2+), Clean Architecture + Domain-Driven Design.

Layers (STRICT):
  src/Application/     — Controllers, Requests, Resources, Jobs, Commands
  src/Domain/          — Actions, DTOs, Enums, Models (business logic only)
  src/Infrastructure/  — Repositories, Services, External Integrations

Fundamental Rules:
- No business logic in Application layer (controllers are thin).
- No database access outside Repositories.
- Domain layer contains business logic only.
- Strict layer separation must always be maintained.
- Implementation order: Enum → Model → Migration → DTO → Repo Interface → Repo Impl → Action → Request → Resource → Controller → Route → Tests.
- PSR-12 compliant. PHP 8.2+ typed. camelCase code, snake_case DB columns, camelCase API responses.
- No magic numbers or strings. No N+1 queries. Use eager loading."""

# ── Output Format (always included) ───────────────────────────────────
OUTPUT_FORMAT = """\
Given the task information below, produce a JSON object with exactly these keys:
- "instructions": A single string with a clear, numbered step-by-step implementation plan. Be specific about which files to create/modify under which src/ paths. Follow the mandatory implementation order. Use "1. ... 2. ... 3. ..." format.
- "acceptance_criteria": A JSON array of strings — testable conditions that confirm the task is done. Include testing requirements (unit tests for Actions, feature tests for API endpoints, >80% coverage target). Keep existing criteria and add missing ones.
- "file_hints": A JSON array of likely file paths following the architecture structure (e.g. "src/Domain/Order/Actions/CreateOrderAction.php"). Keep existing hints and add any you can infer.

Output ONLY valid JSON, no markdown fences, no extra text."""

CLASSIFICATION_GUARDRAILS = """\
Ticket classification rules:
- Classify the ticket before planning. Classification is supplied in the user prompt.
- Only plan a new CRUD/domain scaffold when classification is new_crud_module AND the ticket explicitly asks to create a new entity, module, resource, model, or table.
- Never infer a domain, model, entity, migration, or class name from the Jira issue key alone.
- If classification is not new_crud_module, do not create fake Domain paths, models, migrations, or CRUD actions based on the Jira key.
- For reporting/filter tickets, focus on existing report page/API behavior, backend query filters, dynamic filter options endpoint, frontend filter drawer, branch-dependent options, empty states, and tests."""


def _combined_text(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None = None,
    file_hints: list[str] | None = None,
    meta: dict | None = None,
) -> str:
    parts = [title or "", instructions or ""]
    if acceptance_criteria:
        parts.extend(acceptance_criteria)
    if file_hints:
        parts.extend(file_hints)
    if meta:
        parts.append(json.dumps(meta, ensure_ascii=False))
    return "\n".join(parts).lower()


def classify_task(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None = None,
    file_hints: list[str] | None = None,
    meta: dict | None = None,
) -> TicketClassification:
    """Classify a Jira task before any prompt-generation decisions are made."""
    text = _combined_text(title, instructions, acceptance_criteria, file_hints, meta)

    reporting_terms = ("report", "reports", "analytics", "dashboard")
    filter_terms = (
        "filter",
        "filters",
        "slot",
        "reservation status",
        "payment status",
        "seating area",
        "vat",
        "service charge",
    )
    if any(term in text for term in reporting_terms) and any(term in text for term in filter_terms):
        return "reporting_filter_change"

    new_crud_patterns = (
        r"\b(?:create|add|generate|build)\s+(?:a\s+|an\s+)?(?:new\s+)?crud\b",
        r"\bcreate\s+(?:a\s+|an\s+)?new\s+(?:module|entity|resource|model|table)\b",
        r"\badd\s+(?:a\s+|an\s+)?new\s+(?:module|entity|resource|model|table)\b",
        r"\bgenerate\s+(?:a\s+|an\s+)?(?:crud|module|resource)\b",
        r"\bnew\s+\w+\s+(?:module|entity|resource|model|table)\b",
    )
    if any(re.search(pattern, text) for pattern in new_crud_patterns):
        return "new_crud_module"

    if any(term in text for term in ("bug", "fix", "error", "broken", "regression", "incorrect")):
        return "bug_fix"

    if any(term in text for term in ("frontend", "ui", "page", "drawer", "modal", "screen", "button")):
        return "frontend_ui_change"

    if any(term in text for term in ("api", "endpoint", "query", "repository", "backend", "request")):
        return "backend_api_change"

    if any(term in text for term in ("enhance", "update", "change", "existing", "add filter", "add filters")):
        return "existing_feature_enhancement"

    return "unknown"


def _ticket_key_tokens(title: str | None, instructions: str) -> set[str]:
    text = f"{title or ''} {instructions or ''}"
    keys = set(re.findall(r"\b([A-Z][A-Z0-9]+)-(\d+)\b", text))
    return {prefix.capitalize() + number for prefix, number in keys}


def _contains_forbidden_scaffold(
    text: str,
    title: str | None,
    instructions: str,
    classification: TicketClassification,
) -> bool:
    if classification == "new_crud_module":
        return False

    lowered = text.lower()
    if "crud scaffold" in lowered or "unrelated crud" in lowered:
        return True

    for token in _ticket_key_tokens(title, instructions):
        token_lower = token.lower()
        if f"src/domain/{token_lower}" in lowered:
            return True
        if f"{token_lower}item" in lowered:
            return True
        if re.search(rf"\bcreate\w*{re.escape(token_lower)}\w*(?:migration|model|action)\b", lowered):
            return True
    return False


def _merge_unique(existing: list[str] | None, additions: list[str]) -> list[str]:
    result: list[str] = []
    for item in [*(existing or []), *additions]:
        if item and item not in result:
            result.append(item)
    return result


def _local_enhancement(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None,
    file_hints: list[str] | None,
    classification: TicketClassification,
) -> dict:
    if classification == "reporting_filter_change":
        enhanced_instructions = (
            "1. Locate the existing Reservations Reports page and its backing API/query path; use "
            "the ticket description and file hints as the source of truth instead of creating a new "
            "domain module.\n"
            "2. Extend the backend report query to support slot filter, reservation status filter, "
            "payment status filter, seating area filter, VAT included filter, and service charge "
            "included filter while preserving existing pagination, sorting, authorization, and export "
            "behavior.\n"
            "3. Add or update a dynamic filter options endpoint for the Reservations Reports page so "
            "available slots, statuses, payment statuses, and seating areas are scoped to the selected "
            "branch and any other existing report context.\n"
            "4. Update the frontend filter drawer on the existing Reservations Reports page to show the "
            "new filters, refresh branch-dependent options when branch changes, serialize selected "
            "filters into the API request, and clear/reset filters predictably.\n"
            "5. Handle empty states and loading/error states for filter options and filtered report "
            "results without breaking the existing report layout.\n"
            "6. Add backend feature/unit tests for query filtering and filter-options behavior, plus "
            "frontend tests for the filter drawer, branch-dependent options, empty states, and request "
            "payload generation."
        )
        criteria = _merge_unique(
            acceptance_criteria,
            [
                "Existing Reservations Reports page supports slot filter.",
                "Existing Reservations Reports page supports reservation status filter.",
                "Existing Reservations Reports page supports payment status filter.",
                "Existing Reservations Reports page supports seating area filter.",
                "Existing Reservations Reports page supports VAT included filter.",
                "Existing Reservations Reports page supports service charge included filter.",
                "Filter option values update when the selected branch changes.",
                "Backend and frontend tests cover report query filters, dynamic options, and empty states.",
            ],
        )
        hints = _merge_unique(
            file_hints,
            [
                "Existing Reservations Reports page component",
                "Existing Reservations Reports API/controller",
                "Existing Reservations Reports query/repository",
                "Existing Reservations Reports tests",
            ],
        )
        return {
            "instructions": enhanced_instructions,
            "acceptance_criteria": criteria,
            "file_hints": hints,
        }

    return {
        "instructions": instructions,
        "acceptance_criteria": acceptance_criteria,
        "file_hints": file_hints,
    }


def _select_sections(title: str | None, instructions: str) -> list[str]:
    """
    Weighted keyword matching against documentation sections.

    Scoring:
      +2  keyword found in title
      +1  keyword found in instructions/description
      +3  keyword matches the section's department label

    Returns the content of matched sections (up to MAX_SECTIONS).
    """
    title_lower = (title or "").lower()
    instr_lower = instructions.lower()

    scored: list[tuple[int, str, str]] = []
    for section in SECTIONS:
        keywords: list[str] = section["keywords"]  # type: ignore[assignment]
        department: str = section.get("department", "")  # type: ignore[assignment]
        score = 0
        for kw in keywords:
            if kw in title_lower:
                score += 2
            if kw in instr_lower:
                score += 1
        # Boost if department name appears in ticket text
        if department and department in f"{title_lower} {instr_lower}":
            score += 3
        if score > 0:
            scored.append((score, section["id"], section["content"]))  # type: ignore[arg-type]

    # Sort by weighted score (most relevant first)
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        # No matches — include architecture overview + implementation order as defaults
        defaults = {s["id"]: s["content"] for s in SECTIONS if s["id"] in ("architecture_overview", "implementation_order")}
        return [defaults.get("architecture_overview", ""), defaults.get("implementation_order", "")]

    return [content for _, _, content in scored[:MAX_SECTIONS]]


def _build_system_prompt(title: str | None, instructions: str) -> str:
    """
    Build the system prompt dynamically:
    Core Policy + matched documentation sections + output format.
    """
    selected = _select_sections(title, instructions)

    parts = [CORE_POLICY]
    parts.append("\n--- CLASSIFICATION GUARDRAILS ---")
    parts.append(CLASSIFICATION_GUARDRAILS)
    if selected:
        parts.append("\n--- RELEVANT ARCHITECTURE DOCS ---")
        for section_content in selected:
            parts.append(section_content)
    parts.append("\n--- OUTPUT FORMAT ---")
    parts.append(OUTPUT_FORMAT)

    return "\n\n".join(parts)


def _build_user_prompt(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None,
    file_hints: list[str] | None,
    classification: TicketClassification,
    meta: dict | None = None,
) -> str:
    parts = []
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"Classification: {classification}")
    parts.append(f"Instructions: {instructions}")
    if acceptance_criteria:
        parts.append(f"Existing acceptance criteria: {json.dumps(acceptance_criteria)}")
    if file_hints:
        parts.append(f"Existing file hints: {json.dumps(file_hints)}")
    if meta:
        parts.append(f"Metadata: {json.dumps(meta, ensure_ascii=False)}")
    return "\n".join(parts)


async def enhance_task(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None = None,
    file_hints: list[str] | None = None,
    meta: dict | None = None,
) -> dict:
    """
    Enhance raw task data using OpenAI GPT-5 mini with RAG.

    Only relevant documentation sections are included in the prompt
    (selected by keyword matching), reducing token usage.

    Falls back to original data if API key is missing or call fails.
    """
    classification = classify_task(title, instructions, acceptance_criteria, file_hints, meta)
    fallback = _local_enhancement(
        title,
        instructions,
        acceptance_criteria,
        file_hints,
        classification,
    )

    if not OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set — skipping LLM enhancement")
        return fallback

    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        system_prompt = _build_system_prompt(title, instructions)
        user_prompt = _build_user_prompt(
            title,
            instructions,
            acceptance_criteria,
            file_hints,
            classification,
            meta,
        )

        logger.info(
            "RAG: selected %d sections for ticket '%s'",
            len(_select_sections(title, instructions)),
            title or "(no title)",
        )

        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content.strip()
        result = json.loads(text)
        enhanced_instructions = result.get("instructions", instructions)
        if _contains_forbidden_scaffold(enhanced_instructions, title, instructions, classification):
            logger.warning(
                "LLM output violated non-CRUD guardrails for ticket '%s'; using local fallback",
                title or "(no title)",
            )
            return fallback

        return {
            "instructions": enhanced_instructions,
            "acceptance_criteria": result.get("acceptance_criteria", acceptance_criteria),
            "file_hints": result.get("file_hints", file_hints),
        }

    except Exception:
        logger.exception("LLM enhancement failed — using original data")
        return fallback
