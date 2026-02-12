# Deployment Guide — MCP Server with LLM Enhancement

## What Changed and Why

### The Problem

When Jira issues arrive via n8n webhook, they typically contain raw bug reports or feature requests written by project managers — not developer-ready instructions. Cursor's AI agent receives these as-is, which leads to:

- Vague or incomplete instructions (e.g. "Fix the login bug")
- Missing acceptance criteria — the agent doesn't know when the task is "done"
- No file hints — the agent has to search the entire codebase blindly

### The Solution

We added an **LLM prompt engineering layer** to the MCP server. When Cursor calls `get_task`, the raw Jira data passes through OpenAI's GPT-5 mini model **before** being returned to Cursor. The ingest server stores raw data as-is — the LLM enhancement happens at read time, not write time.

### Before vs After

**Before** (raw Jira issue stored directly):
```json
{
  "instructions": "Users report getting logged out after 5 minutes. Fix session timeout.",
  "acceptance_criteria": null,
  "file_hints": null
}
```

**After** (LLM-enhanced before storage):
```json
{
  "instructions": "1. Locate the session configuration file. 2. Identify the current timeout value (likely 5 min). 3. Update to 30 min or 1 hour depending on security requirements.",
  "acceptance_criteria": [
    "Users remain logged in for at least 30 minutes",
    "Session timeout can be adjusted and changes take effect",
    "Application does not log users out prematurely"
  ],
  "file_hints": ["security_config.json", "auth_settings.py", "session_manager.js"]
}
```

### What Was Modified

| File | Type | Change |
|------|------|--------|
| `llm.py` | NEW | OpenAI-based task enhancement module (GPT-5 mini) |
| `mcp_server.py` | MODIFIED | +1 import, LLM enhancement in `get_task` tool |
| `requirements.txt` | MODIFIED | Added `openai>=1.0.0` |
| `env.example` | MODIFIED | Added `OPENAI_API_KEY` variable |
| `Dockerfile` | MODIFIED | Added `llm.py` to COPY command |
| `docker-compose.yml` | MODIFIED | Added `OPENAI_API_KEY` env var for mcp-server service |
| `.dockerignore` | MODIFIED | Added `test_*.py` exclusion |

### How It Works (Flow)

```
Jira Issue Created
    |
    v
n8n Workflow --> POST /ingest (with X-Ingest-Token header)
    |
    v
Ingest Server: parse & validate payload (Pydantic)
    |
    v
PostgreSQL: store raw task (as-is from n8n)
    |
    v
Cursor: list_tasks -> get_task
    |
    v
NEW: LLM Enhancement (OpenAI / GPT-5 mini)     <-- added step
    |   - Transforms raw instructions into numbered steps
    |   - Generates acceptance_criteria if missing
    |   - Infers file_hints if missing
    |   - Falls back to original data if LLM fails
    |
    v
Cursor receives enhanced task -> apply changes -> complete_task
```

### n8n Workflow Configuration

n8n is the automation bridge between Jira and our MCP server. Here's how the workflow is set up:

**Trigger**: Jira Trigger node — fires when a new issue is created (or updated) in your Jira project.

**HTTP Request node** — sends the Jira issue data to our ingest endpoint:

| Setting | Value |
|---------|-------|
| Method | `POST` |
| URL | `https://your-ec2-domain/ingest` |
| Authentication | None (token is in headers) |
| Headers | `Content-Type: application/json` |
| | `X-Ingest-Token: <your INGEST_TOKEN from .env>` |
| Body Type | JSON |

**Payload structure** (map Jira fields to these keys in the n8n Set/Code node):

```json
{
  "id": "{{ $json.key }}",
  "source": "jira",
  "title": "{{ $json.fields.summary }}",
  "instructions": "{{ $json.fields.description }}",
  "acceptance_criteria": null,
  "file_hints": null,
  "meta": {
    "jira_url": "https://your-org.atlassian.net/browse/{{ $json.key }}",
    "priority": "{{ $json.fields.priority.name }}",
    "assignee": "{{ $json.fields.assignee.displayName }}"
  }
}
```

**Field mapping details**:

| JSON Key | Jira Field | Required | Notes |
|----------|-----------|----------|-------|
| `id` | Issue key (e.g. `PROJ-123`) | Yes | Used as unique task identifier in PostgreSQL |
| `source` | Hardcoded `"jira"` | No | Defaults to `"jira"` if omitted |
| `title` | `fields.summary` | No | Short issue title — helps the LLM understand context |
| `instructions` | `fields.description` | Yes | The raw issue body — this is what the LLM enhances |
| `acceptance_criteria` | — | No | Can be `null` — the LLM will generate these automatically |
| `file_hints` | — | No | Can be `null` — the LLM will infer likely file paths |
| `meta` | Any extra fields | No | Stored as-is in JSONB, useful for traceability back to Jira |

**What happens after n8n sends the request**:

1. Ingest server receives the POST, validates the `X-Ingest-Token` header
2. Parses the body — handles various n8n quirks (string-wrapped JSON, `{"body": "..."}` wrappers, unescaped newlines)
3. Validates the payload with Pydantic (`id` and `instructions` are required, everything else is optional)
4. Stores the raw task in PostgreSQL
5. When Cursor calls `get_task`, the MCP server enhances the task via OpenAI GPT-5 mini before returning it
6. Returns `{"ok": true, "summary": "Wrote PROJ-123"}` — n8n can check this for success

**Duplicate handling**: If n8n sends the same issue `id` twice:
- If the task is still `pending` or `in_progress` — returns a conflict (no overwrite)
- If the task was already `completed` or `failed` — updates it (allows retries)

**n8n workflow diagram**:

```
+------------------+     +------------------+     +--------------------+
|  Jira Trigger    | --> |  Set Node        | --> |  HTTP Request      |
|  (issue created) |     |  (map fields to  |     |  POST /ingest      |
|                  |     |   JSON payload)  |     |  + X-Ingest-Token  |
+------------------+     +------------------+     +--------------------+
                                                           |
                                                           v
                                                  +--------------------+
                                                  |  IF node (optional)|
                                                  |  Check ok == true  |
                                                  +--------------------+
```

### Safety & Fallback

- If `OPENAI_API_KEY` is **not set**: Cursor receives raw task data (original behavior)
- If the OpenAI API **fails** (network error, rate limit): Cursor receives raw task data
- **No database changes needed**: the existing `tasks` table schema already has `instructions` (text), `acceptance_criteria` (JSONB), and `file_hints` (JSONB)
- The LLM step is **non-blocking** — if it fails, `get_task` still returns the raw task data
- Ingest always succeeds regardless of LLM status — raw data is stored reliably

---

## Deployment on EC2

### Prerequisites

- EC2 instance with Docker and Docker Compose installed
- PostgreSQL database (RDS or self-hosted)
- OpenAI API key (get one at https://platform.openai.com/api-keys)

### Step 1: Pull Latest Code

```bash
ssh your-ec2-instance
cd /path/to/mcp-server
git pull origin main
```

### Step 2: Update `.env`

Add the `OPENAI_API_KEY` to your existing `.env` file:

```bash
# View current .env
cat .env

# Add the OpenAI key (append to existing file)
echo 'OPENAI_API_KEY=sk-YOUR_OPENAI_API_KEY_HERE' >> .env
```

Your `.env` should now have these three variables:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@your-db-host:5432/mcp_tasks
INGEST_TOKEN=your-existing-secret-token
OPENAI_API_KEY=sk-YOUR_OPENAI_API_KEY_HERE
```

### Step 3: Rebuild and Restart

```bash
# Rebuild the Docker image (picks up new llm.py + openai dependency)
docker compose build --no-cache

# Restart all services
docker compose up -d
```

### Step 4: Verify Deployment

```bash
# Check all containers are running
docker compose ps

# Check ingest server logs
docker compose logs ingest --tail=20

# Send a test task
curl -X POST http://localhost/ingest \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Token: YOUR_INGEST_TOKEN" \
  -d '{
    "id": "DEPLOY-TEST-001",
    "source": "jira",
    "title": "Test LLM Enhancement",
    "instructions": "Users cannot upload files larger than 5MB. Increase the limit."
  }'

# Expected response: {"ok":true,"summary":"Wrote DEPLOY-TEST-001","path":null}
```

### Step 5: Verify LLM Enhancement in Database

```bash
docker compose exec mcp-server python -c "
import asyncio, database, json
async def check():
    t = await database.get_task('DEPLOY-TEST-001')
    print(json.dumps(t, indent=2))
asyncio.run(check())
"
```

You should see enhanced `instructions` (numbered steps), generated `acceptance_criteria`, and `file_hints` — all produced by the LLM from the minimal input.

### Step 6: Clean Up Test Task

```bash
docker compose exec mcp-server python -c "
import asyncio, database
async def cleanup():
    await database.update_task_status('DEPLOY-TEST-001', 'completed', completion_note='Deployment test')
asyncio.run(cleanup())
"
```

---

## Architecture (After LLM Addition)

```
                    +------------------+
                    |   Jira (Cloud)   |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    |   n8n Workflow    |
                    +--------+---------+
                             |
                             | POST /ingest
                             v
+---------------------------------------------------+
|                  EC2 Instance                      |
|                                                    |
|  nginx:80                                          |
|  +-- /ingest --> ingest:8787                       |
|  |               +-- Pydantic validation           |
|  |               +-- PostgreSQL write (raw data)   |
|  |                                                 |
|  +-- /mcp -----> mcp-server:8000                   |
|                  +-- list_tasks                     |
|                  +-- get_task + LLM enhancement NEW |
|                  +-- start_task                     |
|                  +-- complete_task                  |
|                  +-- fail_task                      |
|                                                    |
|  PostgreSQL:5432 (shared database)                 |
+---------------------------------------------------+
                             |
                             | MCP protocol (HTTP)
                             v
                    +------------------+
                    |  Cursor (User)   |
                    +------------------+
```

---

## Environment Variables Reference

| Variable | Required | Service | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | Both | PostgreSQL connection string |
| `INGEST_TOKEN` | Yes | Ingest | Secret token for webhook authentication |
| `OPENAI_API_KEY` | No | MCP Server | OpenAI API key for LLM enhancement (GPT-5 mini). If empty, raw tasks returned as-is |

---

## LLM Configuration

The LLM enhancement is configured in `llm.py`:

- **Model**: `gpt-5-mini` (OpenAI)
- **Response format**: JSON mode (guaranteed valid JSON)
- **Prompt**: Transforms raw Jira issues into structured developer instructions

### OpenAI API

- Get a key at https://platform.openai.com/api-keys
- GPT-5 mini is a cost-effective model suitable for structured text transformation
- Check pricing and rate limits at https://platform.openai.com/docs/pricing

For higher throughput or different quality/cost tradeoffs, you can switch the model in `llm.py` (e.g. `gpt-4o-mini`, `gpt-4o`).

---

## Troubleshooting

### LLM enhancement not working

1. Check `OPENAI_API_KEY` is set: `docker compose exec mcp-server env | grep OPENAI`
2. Check MCP server logs: `docker compose logs mcp-server --tail=50`
3. Look for "LLM enhancement failed" in logs — the fallback kicks in silently

### Tasks stored without enhancement

This is expected if:
- `OPENAI_API_KEY` is empty or not set
- OpenAI API returned an error (rate limit, network, billing)
- The task is still stored successfully with original data

### 429 Rate Limit from OpenAI

If you're ingesting many tasks at once, some may fall back to raw data. Solutions:
- Check your OpenAI usage tier and rate limits at https://platform.openai.com/settings/organization/limits
- Add a delay between n8n webhook calls
- The system handles this gracefully — no data loss
