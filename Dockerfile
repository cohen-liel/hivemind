# ── Stage 1: Build frontend ──────────────────────────────────────────────────
# Pinned to minor version for reproducible builds (build-only — not in final image)
FROM node:20.18-alpine AS frontend-builder
WORKDIR /frontend

# Install deps first (cached unless package files change)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Then copy source and build
COPY frontend/ .
RUN npx tsc && npx vite build

# ── Stage 2: Python dependency builder ──────────────────────────────────────
# Install Python packages into a separate layer so the final image only
# receives the compiled artifacts, not build tooling.
FROM python:3.11-slim AS py-builder
WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install uv for fast, reproducible installs
RUN pip install --no-cache-dir uv==0.6.10

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# ── Stage 3: Production runtime ──────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Hivemind" \
      org.opencontainers.image.description="AI engineering team orchestrator" \
      org.opencontainers.image.source="https://github.com/cohen-liel/hivemind"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Minimal system deps: curl (health-check probe), nodejs + npm (Claude CLI)
# Combined into single RUN for fewer layers
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       nodejs \
       npm \
    && npm install -g @anthropic-ai/claude-code 2>/dev/null \
       || echo "WARN: Claude CLI npm install failed — set CLAUDE_CLI_PATH if needed" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

# Pull compiled Python packages from builder stage (keeps this layer small)
COPY --from=py-builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=py-builder /usr/local/bin /usr/local/bin

# ── Application source ──────────────────────────────────────────────────────
# Copy in dependency order: least-changing first for better layer caching

# Copy skills directory (agent skill prompts / system messages)
COPY .claude/ ./.claude/

# src/ package — contains api, db, models, storage, workers (critical runtime code)
COPY src/ ./src/

# Dashboard API module
COPY dashboard/ ./dashboard/

# Root-level Python modules
COPY *.py ./

# Copy pre-built frontend assets from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create non-root user and fix ownership BEFORE switching to it
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Health-check: hit the /api/health endpoint (defined in dashboard/api.py)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["python", "server.py"]
