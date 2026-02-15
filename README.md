## BillQode MCP Server

Jira → n8n → MCP Server → Cursor IDE pipeline.

Receives Jira issues via n8n webhook, stores them in PostgreSQL, and serves them to Cursor via MCP tools — enhanced by OpenAI GPT-5 mini with **RAG** (Retrieval-Augmented Generation).

### Architecture

```
Jira Issue
   ↓
n8n Webhook (POST /ingest)
   ↓
ingest_server.py → PostgreSQL (raw task data)
   ↓
Cursor calls get_task via MCP
   ↓
mcp_server.py → RAG keyword match → GPT-5 mini enhancement
   ↓
Enhanced task returned to Cursor
```

### RAG — How It Works

Instead of sending the full architecture handbook (~4000 tokens) with every OpenAI call, the system uses keyword-based retrieval:

1. **Core Policy** (~15 lines) — always included. Enforces fundamental Clean Architecture rules.
2. **Documentation sections** (`rag_docs.py`) — 18 detailed sections covering patterns, integrations, testing, etc.
3. **Keyword matching** — ticket title + instructions are matched against section keywords. Only the top 5 relevant sections are attached.
4. **Result**: ~30-50% fewer tokens per call, with more precise context.

**Files:**
- `rag_docs.py` — all documentation sections organized by department
- `llm.py` — Core Policy, keyword matcher (`_select_sections`), dynamic prompt builder

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
| `llm.py` | OpenAI GPT-5 mini integration with RAG |
| `rag_docs.py` | Documentation sections for RAG retrieval |
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
| `get_task` | Get a task by ID — enhanced by GPT-5 mini via RAG |
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
  "description": "Full description from Jira"
}
```

### Prompt Template (Task-Driven)

> Call MCP tool `list_tasks` (limit=5). Pick the newest task. Then call `get_task` for its id. Use ONLY `task.instructions` (and any structured fields like acceptance criteria / file hints) as the source of truth. Implement the changes in the repo. After you finish, call `complete_task` with the task id and a short note of what changed.

### Test LLM Enhancement

```powershell
python test_llm.py
```

This sends a sample ticket through the RAG pipeline and prints the enhanced output.
