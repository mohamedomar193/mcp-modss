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
You are a senior software engineer assistant. You receive a raw Jira issue and must transform it into clear, actionable developer instructions.

Given the task information below, produce a JSON object with exactly these keys:
- "instructions": A single string with a clear, numbered step-by-step implementation plan for a developer. Be specific about what code to write/change. Use "1. ... 2. ... 3. ..." format inside the string.
- "acceptance_criteria": A JSON array of strings — testable conditions that confirm the task is done. Keep existing criteria and add missing ones.
- "file_hints": A JSON array of likely file paths or filenames the developer should look at. Keep existing hints and add any you can infer.

Rules:
- Keep it concise and practical — no fluff.
- If the original instructions are already detailed, refine them rather than rewrite.
- If acceptance_criteria or file_hints are already provided and good, keep them as-is.
- Output ONLY valid JSON, no markdown fences, no extra text.
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
