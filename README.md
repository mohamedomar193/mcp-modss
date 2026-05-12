## BillQode MCP Server

Jira → n8n → MCP Server → Cursor IDE pipeline.

Receives already-generated Jira task prompts from n8n, stores them in PostgreSQL, and serves them to Cursor via MCP tools exactly as stored.

### Architecture

```
Jira Issue
   ↓
n8n Webhook (POST /ingest)
   ↓
n8n Edit Fields → Message a model → Code/Set → HTTP Request
   ↓
ingest_server.py → PostgreSQL
   ↓
Cursor calls get_task via MCP
   ↓
Stored task returned exactly
```

### Prompt Generation

n8n is responsible for prompt generation. The MCP server does not call OpenAI, run RAG, classify tickets, or rewrite prompts during task retrieval.

The expected workflow is:

```
Jira → n8n Webhook → Edit Fields → Message a model → Code/Set → HTTP Request → MCP ingest
```

`llm.py` and `rag_docs.py` may remain in the repo for reference, but MCP task retrieval does not import or execute them.

### Multi-Department Support

`rag_docs.py` is organized by department for future expansion:

| List | Department | Status |
|------|-----------|--------|
| `BACKEND_SECTIONS` | Laravel 11 / Clean Architecture / DDD | 18 sections |
| `FRONTEND_SECTIONS` | Next.js / React / Vue | Placeholder |
| `MOBILE_SECTIONS` | Flutter / React Native / Swift | Placeholder |
| `DEVOPS_SECTIONS` | CI/CD / Docker / AWS / Terraform | Placeholder |

All lists merge into `SECTIONS` automatically. To add a new department, add sections to the appropriate list with `id`, `department`, `keywords`, and `content`.

### Project Files

| File | Purpose |
|------|---------|
| `mcp_server.py` | MCP server (port 8000) — exposes tools to Cursor |
| `ingest_server.py` | Webhook server (port 8787) — receives from n8n |
| `database.py` | PostgreSQL async connection pool + task CRUD |
| `llm.py` | Legacy/local prompt enhancement helpers; not used by MCP task retrieval |
| `rag_docs.py` | Legacy RAG documentation sections; not used by MCP task retrieval |
| `docker-compose.yml` | Production stack (mcp-server + ingest + nginx) |
| `Dockerfile` | Container image for both servers |

### 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Configure Environment

Copy `env.example` to `.env` and set:

```
DATABASE_URL=postgresql://user:pass@localhost:5432/mcp_tasks
INGEST_TOKEN=change-me-to-a-long-random-secret
OPENAI_API_KEY=sk-...
```

### 3) Run Locally

```powershell
# Terminal 1 — MCP server
uvicorn mcp_server:app --host 0.0.0.0 --port 8000

# Terminal 2 — Ingest webhook
uvicorn ingest_server:app --host 0.0.0.0 --port 8787
```

### 4) Configure Cursor

Copy `mcp.json.example` to `.cursor/mcp.json`, then in Cursor:
- **Settings → MCP** → add server with URL (e.g. `http://localhost:8000/mcp` or `http://your-ec2/mcp`)

### 5) Deploy on EC2

```bash
# On the server
git pull
cp env.example .env   # edit with real values
docker compose build --no-cache
docker compose up -d
```

Cursor connects to `http://your-ec2-ip/mcp`. n8n posts to `http://your-ec2-ip/ingest`.

See `DEPLOYMENT.md` for full deployment guide.

### MCP Tools

| Tool | Description |
|------|-------------|
| `list_tasks` | List tasks from inbox (filter by status) |
| `get_task` | Get a task by ID exactly as stored in PostgreSQL |
| `enqueue_task` | Create a task manually |
| `start_task` | Mark task as in_progress |
| `complete_task` | Mark task as completed |
| `fail_task` | Mark task as failed |

### n8n Webhook Payload

```
POST /ingest
Header: X-Ingest-Token: <secret>
```

```json
{
  "id": "SCRUM-123",
  "summary": "Short title from Jira",
  "source": "jira",
  "instructions": "Generated implementation prompt from n8n",
  "acceptance_criteria": ["Testable condition from Jira"],
  "file_hints": ["Existing file or area to inspect"],
  "issue_type": "Story",
  "labels": ["reports"],
  "components": ["Reservations"],
  "meta": { "priority": "High" }
}
```

`instructions` is preferred and should contain the generated n8n prompt. `description` is accepted
as a fallback for older payloads. If both are missing or empty, `/ingest` returns HTTP 422.

### Prompt Template (Task-Driven)

> Call MCP tool `list_tasks` (limit=5). Pick the newest task. Then call `get_task` for its id. Use ONLY `task.instructions` (and any structured fields like acceptance criteria / file hints) as the source of truth. Implement the changes in the repo. After you finish, call `complete_task` with the task id and a short note of what changed.

### Test Ingest And Retrieval

```powershell
python -m unittest test_pipeline.py
```

This verifies that `/ingest` stores n8n-generated task fields and `get_task` returns them without MCP-side prompt generation.
