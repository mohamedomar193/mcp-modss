"""
Test script for ingest_server webhook endpoint.
Simulates n8n POST requests to test duplicate detection and status tracking.
"""

import json
import requests
from pathlib import Path

# Configuration
INGEST_URL = "http://127.0.0.1:8787/ingest"
INGEST_TOKEN = "change-me-to-a-long-random-secret"  # Must match your INGEST_TOKEN env var

headers = {
    "Content-Type": "application/json",
    "X-Ingest-Token": INGEST_TOKEN,
}


def test_health():
    """Test health endpoint."""
    print("Testing /health endpoint...")
    resp = requests.get("http://127.0.0.1:8787/health")
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)}")
    print()


def test_create_task(task_id: str, title: str, instructions: str):
    """Create a new task."""
    print(f"Creating task {task_id}...")
    payload = {
        "id": task_id,
        "source": "jira",
        "title": title,
        "instructions": instructions,
        "acceptance_criteria": ["Test passes", "Code compiles"],
        "file_hints": ["test_ingest.py"],
    }
    resp = requests.post(INGEST_URL, headers=headers, json=payload)
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)}")
    print()
    return resp.json()


def test_duplicate_detection(task_id: str):
    """Test duplicate detection - should fail if task is pending/in_progress."""
    print(f"Testing duplicate detection for {task_id}...")
    payload = {
        "id": task_id,
        "source": "jira",
        "title": "Duplicate test",
        "instructions": "This should be rejected",
    }
    resp = requests.post(INGEST_URL, headers=headers, json=payload)
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)}")
    print()
    return resp.json()


def check_task_file(task_id: str):
    """Check the actual task file on disk."""
    print(f"Checking task file for {task_id}...")
    task_path = Path("tasks/inbox") / f"{task_id}.json"
    if task_path.exists():
        task_data = json.loads(task_path.read_text())
        print(f"Task file exists:")
        print(f"  Status: {task_data.get('status', 'NOT SET')}")
        print(f"  Created: {task_data.get('created_at', 'NOT SET')}")
        print(f"  Started: {task_data.get('started_at', 'NOT SET')}")
        print(f"  Completed: {task_data.get('completed_at', 'NOT SET')}")
        print(f"  Failed: {task_data.get('failed_at', 'NOT SET')}")
    else:
        print(f"Task file NOT found at {task_path}")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Ingest Server")
    print("=" * 60)
    print()
    
    # Test 1: Health check
    try:
        test_health()
    except requests.exceptions.ConnectionError:
        print("ERROR: Cannot connect to ingest server!")
        print("Make sure ingest_server is running:")
        print("  $env:INGEST_TOKEN='change-me-to-a-long-random-secret'")
        print("  uvicorn ingest_server:app --host 0.0.0.0 --port 8787")
        exit(1)
    
    # Test 2: Create a new task
    task_id = "TEST-001"
    result = test_create_task(
        task_id=task_id,
        title="Test Task",
        instructions="This is a test task to verify the system works."
    )
    
    # Check the file was created
    check_task_file(task_id)
    
    # Test 3: Try to create duplicate (should fail)
    print("=" * 60)
    print("Testing Duplicate Detection")
    print("=" * 60)
    print()
    duplicate_result = test_duplicate_detection(task_id)
    
    if duplicate_result.get("ok") == False:
        print("✓ Duplicate detection working correctly!")
    else:
        print("✗ Duplicate detection failed - task was created when it shouldn't be")
    
    print()
    print("=" * 60)
    print("Test Complete")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Check tasks/inbox/TEST-001.json to verify status field")
    print("2. Use MCP tools in Cursor to test status tracking:")
    print("   - list_tasks()")
    print("   - get_task('TEST-001')")
    print("   - start_task('TEST-001')")
    print("   - complete_task('TEST-001', note='Test completed')")
