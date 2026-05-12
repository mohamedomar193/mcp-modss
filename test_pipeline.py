import asyncio
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

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OpenAI client is not used in these tests")

    openai_stub.AsyncOpenAI = AsyncOpenAI
    sys.modules["openai"] = openai_stub

import ingest_server
import llm


BILQ_2501_DESCRIPTION = """
BILQ-2501: Add advanced filters to the existing Reservations Reports page.
The report should let users filter by slot, reservation status, payment status,
seating area, VAT included, and service charge included. Filter option values
must respect the selected branch.
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
    def test_bilq_2501_like_ticket_is_reporting_filter_change(self) -> None:
        classification = llm.classify_task(
            "BILQ-2501 Advanced filters on Reservations Reports",
            BILQ_2501_DESCRIPTION,
            BILQ_2501_CRITERIA,
        )

        with patch.object(llm, "OPENAI_API_KEY", ""):
            result = asyncio.run(
                llm.enhance_task(
                    "BILQ-2501 Advanced filters on Reservations Reports",
                    BILQ_2501_DESCRIPTION,
                    BILQ_2501_CRITERIA,
                    file_hints=["Existing Reservations Reports page"],
                )
            )

        instructions = result["instructions"]
        lowered = instructions.lower()

        self.assertIn(classification, {"reporting_filter_change", "existing_feature_enhancement"})
        self.assertIn("existing reservations reports page", lowered)
        self.assertIn("slot filter", lowered)
        self.assertIn("reservation status filter", lowered)
        self.assertIn("payment status filter", lowered)
        self.assertIn("seating area filter", lowered)
        self.assertIn("vat included", lowered)
        self.assertIn("service charge included", lowered)
        self.assertNotIn("Bilq2501Item", instructions)
        self.assertNotIn("src/Domain/Bilq2501", instructions)
        self.assertNotIn("crud scaffold", lowered)

    def test_ingest_rejects_missing_description(self) -> None:
        with patch.object(ingest_server, "INGEST_TOKEN", "test-token"):
            client = TestClient(ingest_server.app)
            response = client.post(
                "/ingest",
                headers={"X-Ingest-Token": "test-token"},
                json={"id": "BILQ-2501", "summary": "Advanced filters"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Ticket description is required for MCP enhancement")

    def test_ingest_stores_description_criteria_hints_and_meta(self) -> None:
        calls = []

        async def fake_get_task_status(task_id: str):
            return None

        async def fake_upsert_task(*args, **kwargs):
            calls.append((args, kwargs))

        payload = {
            "id": "BILQ-2501",
            "summary": "Advanced filters on Reservations Reports",
            "source": "jira",
            "description": BILQ_2501_DESCRIPTION,
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
        self.assertEqual(args[2], BILQ_2501_DESCRIPTION.strip())
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


if __name__ == "__main__":
    unittest.main()
