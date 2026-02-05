## Cursor + MCP (this repo)

This repo runs a **local MCP stdio server** that Cursor can call as a tool.

It also supports a simple **task-queue** pattern so external systems (like n8n) can feed structured work items that Cursor can pull via MCP tools.

### 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Configure Cursor to load the MCP tool

Copy `mcp.json.example` to `.cursor/mcp.json`, then in Cursor:
- **Settings → MCP** (or Tools/Integrations → MCP)
- Refresh tools / enable the server if needed

### 3) Set your key

Set `OPENAI_API_KEY` in your environment (recommended), or in `.cursor/mcp.json` via `env`.

### 3b) Run the ingest webhook (for n8n cloud)

If n8n runs in the cloud, it can’t write files to your PC. So you run a small HTTP webhook locally that writes task JSON into `tasks/inbox/`.

In a separate terminal:

```powershell
$env:INGEST_TOKEN="change-me-to-a-long-random-secret"
uvicorn ingest_server:app --host 0.0.0.0 --port 8787
```

Optional health check (in a browser):
- `http://127.0.0.1:8787/health`

### 3c) Expose the webhook with ngrok

In another terminal:

```powershell
.\ngrok.exe http 8787
```

ngrok will print a public HTTPS URL like `https://xxxx.ngrok-free.app`.
Your n8n endpoint becomes:
- `POST https://xxxx.ngrok-free.app/ingest`

### 4) Use it in Composer (Agent)

In **Composer/Agent mode**, ask Cursor to use the MCP tool:

- “Call `generate_code` with `language="python"`, `intent="snippet"`, and my prompt. Use its JSON as the source of truth.”

### Jira → n8n → MCP → Cursor (your flowchart)

Cursor can’t be “pushed” a prompt remotely in a fully headless way; instead, the reliable pattern is **n8n drops a task** somewhere Cursor can see, and **Cursor pulls the task** via MCP when you (or the agent) ask it to.

**Recommended local-first wiring:**

- **n8n**: write a JSON file into `tasks/inbox/` in this repo (via git commit/PR, a shared folder, or any file drop mechanism).
- **n8n cloud**: POST the JSON to your local webhook `/ingest` (exposed via ngrok), which writes into `tasks/inbox/`.
- **MCP server**: exposes tools to list/load/complete tasks (`list_tasks`, `get_task`, `complete_task`).
- **Cursor (Agent)**: calls `list_tasks` → `get_task` → uses the returned `task.instructions` to generate/apply changes → `complete_task`.

### Deploy MCP server on EC2

Run the MCP server over HTTP on your server (e.g. EC2):

1. **On the server:** set `DATABASE_URL` (and optionally `INGEST_TOKEN`) in the environment, then:
   ```bash
   uvicorn mcp_server:app --host 0.0.0.0 --port 8000
   ```
   The MCP endpoint is **POST /mcp**. Expose port 8000 (and use HTTPS in front if needed).
2. **In Cursor:** use **Settings → MCP** and add a server with `url` (e.g. `https://your-ec2/mcp`) and optional `headers` (e.g. `Authorization: Bearer YOUR_API_KEY`).


### n8n HTTP Request node (cloud) — exact config

- **Method**: `POST`
- **URL**: `https://xxxx.ngrok-free.app/ingest`
- **Headers**:
  - `Content-Type`: `application/json`
  - `X-Ingest-Token`: `change-me-to-a-long-random-secret` (must match your PC `INGEST_TOKEN`)
- **Body** (JSON):

```json
{
  "id": "JIRA-123",
  "source": "jira",
  "title": "Short summary from Jira",
  "instructions": "Clear step-by-step instructions for the code change",
  "acceptance_criteria": ["..."],
  "file_hints": ["..."],
  "meta": { "jira_url": "..." }
}
```

### Prompt templates (reliable tool usage)

**Force the tool call**

> Before answering, call the MCP tool `generate_code` with:
> - language: "<language>"
> - intent: "patch"
> - prompt: "<my request>"
> Then, parse the returned JSON. If `ok` is false, stop and ask me what to do next. If `ok` is true, use `code` as the output.

**No guessing**

> You must not guess. If the MCP tool output is missing fields or is invalid JSON, retry once with a tighter prompt; if it still fails, stop and ask me.

### Prompt template (task-driven)

> Call MCP tool `list_tasks` (limit=5). Pick the newest task. Then call `get_task` for its id. Use ONLY `task.instructions` (and any structured fields like acceptance criteria / file hints) as the source of truth. Implement the changes in the repo. After you finish, call `complete_task` with the task id and a short note of what changed.


