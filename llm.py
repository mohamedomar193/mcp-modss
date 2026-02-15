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

from openai import AsyncOpenAI

from rag_docs import SECTIONS

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAX_SECTIONS = 5  # Cap retrieved sections to control prompt size

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
) -> str:
    parts = []
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"Instructions: {instructions}")
    if acceptance_criteria:
        parts.append(f"Existing acceptance criteria: {json.dumps(acceptance_criteria)}")
    if file_hints:
        parts.append(f"Existing file hints: {json.dumps(file_hints)}")
    return "\n".join(parts)


async def enhance_task(
    title: str | None,
    instructions: str,
    acceptance_criteria: list[str] | None = None,
    file_hints: list[str] | None = None,
) -> dict:
    """
    Enhance raw task data using OpenAI GPT-5 mini with RAG.

    Only relevant documentation sections are included in the prompt
    (selected by keyword matching), reducing token usage.

    Falls back to original data if API key is missing or call fails.
    """
    fallback = {
        "instructions": instructions,
        "acceptance_criteria": acceptance_criteria,
        "file_hints": file_hints,
    }

    if not OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set — skipping LLM enhancement")
        return fallback

    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        system_prompt = _build_system_prompt(title, instructions)
        user_prompt = _build_user_prompt(title, instructions, acceptance_criteria, file_hints)

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

        return {
            "instructions": result.get("instructions", instructions),
            "acceptance_criteria": result.get("acceptance_criteria", acceptance_criteria),
            "file_hints": result.get("file_hints", file_hints),
        }

    except Exception:
        logger.exception("LLM enhancement failed — using original data")
        return fallback
