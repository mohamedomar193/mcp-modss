# EC2 Deployment Guide — BillQode MCP Server

## Architecture

```
EC2 Instance
├── nginx (host, port 80)
│   ├── /mcp     → 127.0.0.1:3000  (MCP server — Cursor connects here)
│   └── /ingest  → 127.0.0.1:3001  (ingest server — n8n posts here)
├── Docker: mcp-server   (:3000)  — MCP tools + QA guidelines + Jira reporter
├── Docker: ingest       (:3001)  — webhook receiver (Jira → PostgreSQL)
└── Docker: postgres     (:5432)  — task + test_failures storage
```

nginx runs **on the host** (not in Docker) and proxies to the two containers.

---

## Step 1 — Launch EC2

1. **AWS Console → EC2 → Launch Instance**
2. Settings:
   - **AMI**: Amazon Linux 2023 (recommended) or Ubuntu 22.04
   - **Instance type**: `t3.small` minimum, `t3.medium` recommended
   - **Storage**: 20 GB gp3
   - **Security Group**: open ports `22` (SSH) and `80` (HTTP)
3. Download your `.pem` key pair

---

## Step 2 — SSH into the Instance

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
```

> Use `ubuntu@` instead of `ec2-user@` on Ubuntu AMIs.

---

## Step 3 — Install Docker, Docker Compose, nginx, git

**Amazon Linux 2023:**
```bash
sudo yum update -y
sudo yum install -y docker git nginx

sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

exit   # re-login for group change
```

SSH back in and verify:
```bash
docker --version
docker compose version
nginx -v
```

**Ubuntu 22.04:**
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 nginx git
sudo systemctl enable docker nginx
sudo usermod -aG docker ubuntu
exit
```

---

## Step 4 — Clone the Repo

```bash
git clone https://github.com/mohamedomar193/mcp-server-1-prompt-.git mcp-server
cd mcp-server
```

---

## Step 5 — Create `.env`

```bash
cp env.example .env
nano .env
```

Fill in **all required values**:

```env
# Database password used by Docker containers
POSTGRES_PASSWORD=use-a-strong-random-password

# Webhook auth token — n8n sends this in X-Ingest-Token header
INGEST_TOKEN=use-a-long-random-secret

# OpenAI key for LLM task enhancement (optional — tasks work without it)
OPENAI_API_KEY=sk-proj-...

# Jira integration — required for report_failed_test MCP tool
JIRA_BASE_URL=https://billqode.atlassian.net
JIRA_PROJECT_KEY=BILQ
JIRA_EMAIL=your-email@billqode.com
JIRA_API_TOKEN=your-jira-api-token
```

> **Get a Jira API token:** https://id.atlassian.com/manage-profile/security/api-tokens
>
> `DATABASE_URL` is built automatically inside docker-compose from `POSTGRES_PASSWORD`. Do not set it manually.

Save and exit: `Ctrl+X` → `Y` → `Enter`

---

## Step 6 — Configure nginx

Copy the repo's nginx config to the host:

```bash
sudo cp nginx/nginx.conf /etc/nginx/nginx.conf
sudo nginx -t          # verify config is valid
sudo systemctl enable nginx
sudo systemctl start nginx
```

The config proxies:
- `http://YOUR_IP/mcp`    → `127.0.0.1:3000` (MCP server)
- `http://YOUR_IP/ingest` → `127.0.0.1:3001` (ingest server)

---

## Step 7 — Build & Start Docker Containers

```bash
docker compose build --no-cache
docker compose up -d
```

Wait ~20 seconds for PostgreSQL to start and run `01-schema.sql` (creates `tasks` and `test_failures` tables automatically).

---

## Step 8 — Verify Everything is Running

```bash
# All 3 containers should be Up
docker compose ps
```

Expected:
```
NAME                       STATUS
mcp-server-postgres-1      Up (healthy)
mcp-server-mcp-server-1    Up (healthy)
mcp-server-ingest-1        Up (healthy)
```

```bash
# nginx must be active
sudo systemctl status nginx
```

```bash
# Ingest health check
curl http://localhost/ingest/health
# → {"ok":true,"service":"ingest","storage":"postgres"}
```

```bash
# MCP tools list
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# → JSON listing: list_tasks, get_task, enqueue_task, start_task, complete_task, fail_task, report_failed_test
```

---

## Step 9 — Test the Full Pipeline

### 9a — Ingest a task via webhook
```bash
curl -X POST http://localhost/ingest \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Token: YOUR_INGEST_TOKEN" \
  -d '{
    "id": "DEPLOY-TEST-001",
    "summary": "Verify deployment works",
    "source": "jira",
    "description": "End-to-end deployment test task"
  }'
# → {"ok":true,"summary":"Wrote DEPLOY-TEST-001","path":null}
```

### 9b — Retrieve the task via MCP
```bash
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_task","arguments":{"task_id":"DEPLOY-TEST-001"}}}'
```

### 9c — Report a failed test (creates Jira bug)
```bash
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{
      "name":"report_failed_test",
      "arguments":{
        "test_name": "BILQ-999: Login flow smoke test",
        "failure_reason": "Element not found: getByRole button Login",
        "severity": "High",
        "story_key": "BILQ-100"
      }
    }
  }'
# → {"ok":true,"failure_id":1,"jira_issue_key":"BILQ-XXXX","jira_issue_url":"...","linked_to_story":"BILQ-100",...}
```

### 9d — Clean up test task
```bash
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"complete_task","arguments":{"task_id":"DEPLOY-TEST-001","note":"Deployment verified"}}}'
```

---

## Step 10 — Connect Cursor

In your project's `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "local-codegen": {
      "url": "http://YOUR_EC2_PUBLIC_IP/mcp"
    }
  }
}
```

**Cursor → Settings → MCP → Refresh tools.**

Available tools: `list_tasks`, `get_task`, `enqueue_task`, `start_task`, `complete_task`, `fail_task`, `report_failed_test`

The QA Playwright guidelines are sent to Cursor automatically on connection (no tool call needed). Cursor will follow the Billqode locator priority rules, POM structure, and Chakra UI patterns for any test code it writes.

---

## Step 11 — Connect n8n

Configure an HTTP Request node in your n8n workflow:

| Setting | Value |
|---------|-------|
| Method | `POST` |
| URL | `http://YOUR_EC2_PUBLIC_IP/ingest` |
| Header | `Content-Type: application/json` |
| Header | `X-Ingest-Token: <your INGEST_TOKEN>` |

Jira payload mapping:
```json
{
  "id": "{{ $json.key }}",
  "summary": "{{ $json.fields.summary }}",
  "source": "jira",
  "description": "{{ $json.fields.description }}"
}
```

---

## Updating After Code Changes

```bash
cd ~/mcp-server
git pull
docker compose build --no-cache
docker compose up -d
```

nginx does not need to restart unless `nginx/nginx.conf` changed.

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password for all containers |
| `INGEST_TOKEN` | Yes | Bearer token for the `/ingest` webhook endpoint |
| `OPENAI_API_KEY` | No | GPT-5 mini key for LLM task enhancement. Omit for raw tasks |
| `JIRA_BASE_URL` | For Jira | Your Atlassian URL, e.g. `https://billqode.atlassian.net` |
| `JIRA_PROJECT_KEY` | For Jira | Project key, e.g. `BILQ` |
| `JIRA_EMAIL` | For Jira | Email address linked to the API token |
| `JIRA_API_TOKEN` | For Jira | Jira API token from https://id.atlassian.com |

---

## Troubleshooting

### View logs
```bash
docker compose logs mcp-server --tail=50
docker compose logs ingest --tail=50
docker compose logs postgres --tail=20
sudo journalctl -u nginx --no-pager -n 30
```

### Jira bugs not being created
```bash
# Verify env vars reached the container
docker compose exec mcp-server env | grep JIRA

# Check mcp-server logs for Jira errors
docker compose logs mcp-server | grep -i "jira\|error"
```

### Database issues
```bash
# Check postgres health
docker compose ps postgres

# Manual query
docker compose exec postgres psql -U postgres -d mcp_tasks \
  -c "SELECT count(*) FROM tasks; SELECT count(*) FROM test_failures;"
```

### nginx not proxying
```bash
sudo nginx -t                   # validate config
sudo systemctl restart nginx
curl -v http://localhost/mcp    # check upstream connection
```

### Reset database (destructive)
```bash
docker compose down -v    # removes postgres volume — all data lost
docker compose up -d      # fresh start, schema recreated automatically
```

---

## Database Tables

```sql
-- Task queue (created by n8n/Jira ingest)
tasks (id, source, title, instructions, acceptance_criteria, file_hints, meta,
       status, created_at, started_at, completed_at, failed_at,
       completion_note, failure_reason, updated_at, previous_status)

-- Playwright test failure reports (created by report_failed_test tool)
test_failures (id, task_id, test_name, failure_reason, severity,
               screenshot_path, video_path, logs,
               jira_issue_key, jira_issue_url, created_at)
```
