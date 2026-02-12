# MCP server — HTTP only. Run with: docker run -e DATABASE_URL=... -p 8000:8000 <image>
# For production stack (nginx + app), use docker-compose.

FROM python:3.12-slim

# Prefer non-interactive and avoid writing .pyc / __pycache__
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (mcp_server, ingest_server, database; .dockerignore excludes the rest)
COPY mcp_server.py ingest_server.py database.py llm.py ./

# Run as non-root
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Allow orchestrators to know when the app is ready (port 8000 accepting connections)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',8000)); s.close()" || exit 1

CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
