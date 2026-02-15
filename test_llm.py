"""
Quick standalone test for the LLM enhancement — no database needed.
Usage: python test_llm.py YOUR_OPENAI_API_KEY
"""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Allow setting key via command line for quick testing
if len(sys.argv) > 1:
    os.environ["OPENAI_API_KEY"] = sys.argv[1]

from llm import enhance_task


async def main():
    # Simulate a raw Jira issue with minimal info
    result = await enhance_task(
        title="Login session expires too early",
        instructions="Users report that they get logged out after 5 minutes. Fix the session timeout.",
        acceptance_criteria=None,
        file_hints=None,
    )

    print("=== LLM Enhanced Task ===")
    print(json.dumps(result, indent=2))
    print()

    if result["acceptance_criteria"]:
        print("acceptance_criteria generated OK")
    else:
        print("acceptance_criteria missing")

    if result["file_hints"]:
        print("file_hints generated OK")
    else:
        print("file_hints missing")


if __name__ == "__main__":
    asyncio.run(main())
