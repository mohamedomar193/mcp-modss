# Full EC2 Deployment Guide — BillQode MCP Server

Complete step-by-step guide to deploy the MCP server stack on an AWS EC2 instance.

## What Gets Deployed

```
EC2 Instance (port 80)
├── nginx          → reverse proxy (:80)
│   ├── /mcp       → mcp-server (:8000)  — Cursor connects here
│   └── /ingest    → ingest (:8787)      — n8n posts here
├── mcp-server     → MCP tools + RAG + GPT-5 mini enhancement
├── ingest         → webhook receiver (raw Jira data → PostgreSQL)
└── postgres       → task storage (:5432)
```

---

## Step 1 — Launch EC2 Instance

1. Go to **AWS Console → EC2 → Launch Instance**
2. Settings:
   - **AMI**: Amazon Linux 2023 or Ubuntu 22.04
   - **Instance type**: `t3.small` (minimum) or `t3.medium` (recommended)
   - **Storage**: 20 GB gp3
   - **Security Group**: open ports **22** (SSH) and **80** (HTTP)
3. Download the key pair (`.pem` file)

---

## Step 2 — SSH into EC2

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
```

(Use `ubuntu@` instead of `ec2-user@` if you chose Ubuntu.)

---

## Step 3 — Install Docker & Docker Compose

**Amazon Linux 2023:**
```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# Install Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Re-login for group change
exit
```

SSH back in:
```bash
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
docker --version
docker compose version
```

**Ubuntu 22.04:**
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo systemctl enable docker
sudo usermod -aG docker ubuntu
exit
```

SSH back in and verify.

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

Fill in your values:
```env
POSTGRES_PASSWORD=your-strong-db-password-here
INGEST_TOKEN=your-long-random-secret-here
OPENAI_API_KEY=sk-proj-your-openai-key-here
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

**Important**: `DATABASE_URL` is built automatically in `docker-compose.yml` from `POSTGRES_PASSWORD`. You do NOT need to set `DATABASE_URL` in `.env` for Docker deployment.

---

## Step 6 — Build & Start

```bash
docker compose build --no-cache
docker compose up -d
```

Wait ~30 seconds for PostgreSQL to initialize and create the `tasks` table.

---

## Step 7 — Verify All Services

```bash
# Check all 4 containers are running
docker compose ps
```

Expected output:
```
NAME                       STATUS
mcp-server-postgres-1      Up (healthy)
mcp-server-mcp-server-1    Up
mcp-server-ingest-1        Up (healthy)
mcp-server-nginx-1         Up
```

```bash
# Check ingest health
curl http://localhost/ingest/health
# Expected: {"ok":true,"service":"ingest","storage":"postgres"}
```

```bash
# Check MCP server responds
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_tasks","arguments":{"limit":5}}}'
# Expected: JSON with "ok": true
```

---

## Step 8 — Test Full Pipeline

### 8a. Ingest a test task
```bash
curl -X POST http://localhost/ingest \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Token: YOUR_INGEST_TOKEN" \
  -d '{
    "id": "DEPLOY-TEST-001",
    "summary": "Create Order CRUD API",
    "source": "jira",
    "description": "Implement full CRUD endpoints for orders module with validation"
  }'
# Expected: {"ok":true,"summary":"Wrote DEPLOY-TEST-001","path":null}
```

### 8b. Retrieve with RAG + LLM enhancement
```bash
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_task","arguments":{"task_id":"DEPLOY-TEST-001"}}}'
```

You should see enhanced `instructions` (numbered steps following BillQode architecture), generated `acceptance_criteria`, and `file_hints`.

### 8c. Clean up test task
```bash
curl -X POST http://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"complete_task","arguments":{"task_id":"DEPLOY-TEST-001","note":"Deployment test passed"}}}'
```

---

## Step 9 — Connect Cursor

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

Then in Cursor: **Settings → MCP** → refresh tools. You should see `list_tasks`, `get_task`, `enqueue_task`, `start_task`, `complete_task`, `fail_task`.

---

## Step 10 — Connect n8n

In your n8n workflow, configure the HTTP Request node:

| Setting | Value |
|---------|-------|
| Method | `POST` |
| URL | `http://YOUR_EC2_PUBLIC_IP/ingest` |
| Headers | `Content-Type: application/json` |
| | `X-Ingest-Token: <your INGEST_TOKEN from .env>` |

Payload (map Jira fields):
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

---

## Troubleshooting

### Check logs
```bash
docker compose logs mcp-server --tail=50
docker compose logs ingest --tail=50
docker compose logs postgres --tail=50
docker compose logs nginx --tail=20
```

### LLM enhancement not working
```bash
# Verify OPENAI_API_KEY is set in mcp-server container
docker compose exec mcp-server env | grep OPENAI

# Check for errors
docker compose logs mcp-server | grep -i "llm\|openai\|error"
```

If `OPENAI_API_KEY` is empty, tasks are returned without enhancement (graceful fallback).

### Database connection issues
```bash
# Check postgres is healthy
docker compose ps postgres

# Connect to database manually
docker compose exec postgres psql -U postgres -d mcp_tasks -c "SELECT count(*) FROM tasks;"
```

### Reset database (destructive — deletes all tasks)
```bash
docker compose down -v    # removes volumes including postgres data
docker compose up -d      # fresh start, 01-schema.sql runs again
```

---

## Environment Variables

| Variable | Required | Where Used | Description |
|----------|----------|------------|-------------|
| `POSTGRES_PASSWORD` | Yes | postgres, mcp-server, ingest | Database password |
| `INGEST_TOKEN` | Yes | ingest | Webhook auth token (X-Ingest-Token header) |
| `OPENAI_API_KEY` | No | mcp-server | GPT-5 mini key for RAG enhancement. If empty, raw tasks returned |

---

## Architecture Flow

```
Jira Issue
   ↓
n8n Webhook (POST /ingest + X-Ingest-Token)
   ↓
nginx (:80) → ingest (:8787)
   ↓
PostgreSQL (raw task data stored)
   ↓
Cursor calls get_task via MCP
   ↓
nginx (:80) → mcp-server (:8000)
   ↓
RAG: keyword-match ticket → select relevant architecture docs
   ↓
GPT-5 mini: Core Policy + matched docs → enhanced instructions
   ↓
Enhanced task returned to Cursor
```
