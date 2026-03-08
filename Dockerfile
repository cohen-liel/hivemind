# ── Stage 1: Build frontend ──────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npx tsc && npx vite build

# ── Stage 2: Install Claude CLI ──────────────────────────────────────
FROM node:20-alpine AS cli-builder
RUN npm install -g @anthropic-ai/claude-code 2>/dev/null || true

# ── Stage 3: Python runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# System deps (curl for health check, Node.js for Claude CLI)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code 2>/dev/null \
    || echo "WARN: Claude CLI npm install failed — set CLAUDE_CLI_PATH if needed"

# Install Python deps (layer-cached separately from app code)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py isolated_query.py orchestrator.py scheduler.py sdk_client.py server.py \
     session_manager.py skills_registry.py state.py ./
COPY dashboard/ ./dashboard/

# Copy skills directory (agent skill prompts)
COPY .claude/ ./.claude/

# Copy pre-built frontend from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["python", "server.py"]
