-- Auto-run by PostgreSQL container on first boot (mounted via docker-compose).
-- Creates the tasks table used by mcp_server and ingest_server.

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    source TEXT,
    title TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    acceptance_criteria JSONB,
    file_hints JSONB,
    meta JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    completion_note TEXT,
    failure_reason TEXT,
    updated_at TIMESTAMPTZ,
    previous_status TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC);

CREATE TABLE IF NOT EXISTS test_failures (
    id SERIAL PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    test_name TEXT NOT NULL,
    failure_reason TEXT,
    severity TEXT DEFAULT 'Medium',
    screenshot_path TEXT,
    video_path TEXT,
    logs TEXT,
    jira_issue_key TEXT,
    jira_issue_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_test_failures_task_id ON test_failures (task_id);
CREATE INDEX IF NOT EXISTS idx_test_failures_created_at ON test_failures (created_at DESC);
