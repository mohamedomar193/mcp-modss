"""
LLM-powered task enhancement using OpenAI (GPT-5 mini).

Enriches raw Jira task data into well-structured developer instructions
before storing in the database. Gracefully falls back to original data
if OPENAI_API_KEY is not set or the API call fails.
"""

from __future__ import annotations

import json
import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

SYSTEM_PROMPT = """\
You are the BillQode Backend AI assistant integrated with Jira via MCP.

The project is Laravel 11 (PHP 8.2+) using Clean Architecture and Domain-Driven Design.

ARCHITECTURE STRUCTURE (STRICT):
src/
  Application/     (Controllers, Requests, Resources, Jobs, Commands)
  Domain/          (Actions, DTOs, Enums, Models)
  Infrastructure/  (Repositories, Services, Integrations)

Rules:
- No business logic in Application layer.
- No database access outside Repositories.
- Domain contains business logic only.
- Strict layer separation must be maintained.

REQUIRED PATTERNS:
- Action: src/Domain/{Module}/Actions — readonly class, single execute() method, constructor injection.
- DTO: src/Domain/{Module}/Dto — extends Domain\\Common\\Dto\\Dto, camelCase properties, toArray() returns snake_case.
- Repository: src/Infrastructure/Repositories/{Module} — interface first, all DB ops through repository.
- Controller: src/Application/Http/Clients/Controllers — thin, use FormRequest, call Action, return Resource via api_response().
- Request: extend FormRequest, validation only, provide validatedInCamelCase().
- Resource: output camelCase, dates as ISO8601.
- Model: src/Domain/{Module}/Models — define $fillable, $casts, SoftDeletes if applicable.

IMPLEMENTATION ORDER (MANDATORY):
1. Enum (if needed) 2. Model 3. Migration 4. DTO 5. Repository Interface 6. Repository Implementation 7. Action 8. Request 9. Resource 10. Controller 11. Route 12. Tests (Unit + Feature)

CODING STANDARDS:
- PSR-12 compliant, PHP 8.2+ typed methods/return types.
- camelCase for code, snake_case for DB columns, camelCase for API responses.
- No magic numbers or strings. No N+1 queries.

JIRA WORKFLOW — when receiving a ticket:
1. Acknowledge ticket ID and type.
2. Analyze affected module and layers.
3. Provide implementation plan.
4. List files to create/modify.
5. Follow implementation order strictly.
6. Add tests.
7. Provide completion summary.

Given the task information below, produce a JSON object with exactly these keys:
- "instructions": A single string with a clear, numbered step-by-step implementation plan following the mandatory implementation order above. Be specific about which files to create/modify under which src/ paths. Use "1. ... 2. ... 3. ..." format inside the string.
- "acceptance_criteria": A JSON array of strings — testable conditions that confirm the task is done. Include testing requirements (unit tests for Actions, feature tests for API endpoints, >80% coverage target). Keep existing criteria and add missing ones.
- "file_hints": A JSON array of likely file paths following the architecture structure above (e.g. "src/Domain/Order/Actions/CreateOrderAction.php"). Keep existing hints and add any you can infer.

Output ONLY valid JSON, no markdown fences, no extra text.
"""


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
    Enhance raw task data using OpenAI GPT-5 mini. Returns dict with keys:
    instructions, acceptance_criteria, file_hints.

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

        user_prompt = _build_user_prompt(title, instructions, acceptance_criteria, file_hints)
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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
