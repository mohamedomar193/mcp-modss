import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    asyncpg_stub.Record = dict
    sys.modules["asyncpg"] = asyncpg_stub

import ingest_server
import mcp_server


GENERATED_INSTRUCTIONS = """  1. Update the existing Reservations Reports page.
2. Add slot, reservation status, payment status, seating area, VAT included, and service charge filters.
3. Keep all existing report behavior intact.
"""

BILQ_2501_CRITERIA = [
    "Slot filter is available on Reservations Reports.",
    "Reservation status filter is available on Reservations Reports.",
    "Payment status filter is available on Reservations Reports.",
    "Seating area filter is available on Reservations Reports.",
    "VAT included filter is available on Reservations Reports.",
    "Service charge included filter is available on Reservations Reports.",
]


class PipelineTests(unittest.TestCase):
    def test_ingest_rejects_missing_instructions_and_description(self) -> None:
        with patch.object(ingest_server, "INGEST_TOKEN", "test-token"):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json={"id": "BILQ-2501", "summary": "Advanced filters"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Task instructions or description is required")

    def test_ingest_rejects_empty_generated_json_string(self) -> None:
        with patch.object(ingest_server, "INGEST_TOKEN", "test-token"):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json={
                    "id": "BILQ-2501",
                    "title": "Advanced filters",
                    "instructions": '{"instructions":"","acceptance_criteria":[],"file_hints":[]}',
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Task instructions or description is required")

    def test_ingest_parses_generated_json_string_when_present(self) -> None:
        calls = []

        async def fake_get_task_status(task_id: str):
            return None

        async def fake_upsert_task(*args, **kwargs):
            calls.append((args, kwargs))

        payload = {
            "id": "BILQ-2501",
            "title": "Advanced filters",
            "source": "jira",
            "instructions": json.dumps(
                {
                    "instructions": GENERATED_INSTRUCTIONS,
                    "acceptance_criteria": BILQ_2501_CRITERIA,
                    "file_hints": ["resources/js/pages/reports/reservations"],
                }
            ),
        }

        with (
            patch.object(ingest_server, "INGEST_TOKEN", "test-token"),
            patch.object(ingest_server.database, "get_task_status", fake_get_task_status),
            patch.object(ingest_server.database, "upsert_task", fake_upsert_task),
        ):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        args, kwargs = calls[0]
        self.assertEqual(args[2], GENERATED_INSTRUCTIONS)
        self.assertEqual(kwargs["acceptance_criteria"], BILQ_2501_CRITERIA)
        self.assertEqual(kwargs["file_hints"], ["resources/js/pages/reports/reservations"])

    def test_ingest_preserves_linked_jira_test_cases_in_meta(self) -> None:
        calls = []

        async def fake_get_task_status(task_id: str):
            return None

        async def fake_upsert_task(*args, **kwargs):
            calls.append((args, kwargs))

        linked_test_cases = [
            {
                "key": "BILQ-T123",
                "title": "Verify advanced filters are applied",
                "steps": ["Open Reservations Reports", "Apply slot filter"],
                "expected_result": "Report results are filtered by slot",
            }
        ]

        payload = {
            "id": "BILQ-2501",
            "title": "Advanced filters",
            "source": "jira",
            "instructions": GENERATED_INSTRUCTIONS,
            "test_cases": linked_test_cases,
            "meta": {"priority": "High"},
        }

        with (
            patch.object(ingest_server, "INGEST_TOKEN", "test-token"),
            patch.object(ingest_server.database, "get_task_status", fake_get_task_status),
            patch.object(ingest_server.database, "upsert_task", fake_upsert_task),
        ):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        _, kwargs = calls[0]
        self.assertEqual(
            kwargs["meta"],
            {
                "priority": "High",
                "jira_test_cases": linked_test_cases,
            },
        )

    def test_ingest_accepts_instructions_and_stores_exact_task_fields(self) -> None:
        calls = []

        async def fake_get_task_status(task_id: str):
            return None

        async def fake_upsert_task(*args, **kwargs):
            calls.append((args, kwargs))

        payload = {
            "id": "BILQ-2501",
            "title": "Advanced filters on Reservations Reports",
            "source": "jira",
            "instructions": GENERATED_INSTRUCTIONS,
            "acceptance_criteria": BILQ_2501_CRITERIA,
            "file_hints": ["resources/js/pages/reports/reservations"],
            "issue_type": "Story",
            "labels": ["reports", "filters"],
            "components": ["Reservations"],
            "meta": {"priority": "High"},
        }

        with (
            patch.object(ingest_server, "INGEST_TOKEN", "test-token"),
            patch.object(ingest_server.database, "get_task_status", fake_get_task_status),
            patch.object(ingest_server.database, "upsert_task", fake_upsert_task),
        ):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(len(calls), 1)

        args, kwargs = calls[0]
        self.assertEqual(args[0], "BILQ-2501")
        self.assertEqual(args[1], "Advanced filters on Reservations Reports")
        self.assertEqual(args[2], GENERATED_INSTRUCTIONS)
        self.assertEqual(kwargs["source"], "jira")
        self.assertEqual(kwargs["acceptance_criteria"], BILQ_2501_CRITERIA)
        self.assertEqual(kwargs["file_hints"], ["resources/js/pages/reports/reservations"])
        self.assertEqual(
            kwargs["meta"],
            {
                "priority": "High",
                "issue_type": "Story",
                "labels": ["reports", "filters"],
                "components": ["Reservations"],
            },
        )

    def test_get_task_returns_stored_task_without_llm_rewrite(self) -> None:
        stored_task = {
            "id": "BILQ-2501",
            "source": "jira",
            "title": "Advanced filters on Reservations Reports",
            "instructions": GENERATED_INSTRUCTIONS,
            "acceptance_criteria": BILQ_2501_CRITERIA,
            "file_hints": ["resources/js/pages/reports/reservations"],
            "meta": {"issue_type": "Story"},
            "status": "pending",
        }

        async def fake_get_task(task_id: str):
            self.assertEqual(task_id, "BILQ-2501")
            return dict(stored_task)

        with patch.object(mcp_server.database, "get_task", fake_get_task):
            response = asyncio.run(mcp_server.get_task("BILQ-2501"))

        parsed = json.loads(response)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["task"]["instructions"], GENERATED_INSTRUCTIONS)
        self.assertEqual(parsed["task"]["acceptance_criteria"], BILQ_2501_CRITERIA)
        self.assertEqual(parsed["task"]["file_hints"], ["resources/js/pages/reports/reservations"])
        self.assertFalse(hasattr(mcp_server, "enhance_task"))


if __name__ == "__main__":
    unittest.main()
