# hivemind — Developer Makefile
# ──────────────────────────────────────────────────────────────────────────────
# Usage:
#   make install     — bootstrap a local venv and install all deps
#   make dev         — start the full stack with docker compose (hot-reload)
#   make test        — run the full pytest suite with coverage
#   make lint        — ruff check + format check
#   make typecheck   — mypy static analysis
#   make build       — build the production Docker image
#   make clean       — remove venv and build artefacts

PYTHON    ?= python3.11
VENV      := .venv
PIP       := $(VENV)/bin/pip
PYTEST    := $(VENV)/bin/pytest
RUFF      := $(VENV)/bin/ruff
MYPY      := $(VENV)/bin/mypy
UVICORN   := $(VENV)/bin/uvicorn

.DEFAULT_GOAL := help

# ── Help ─────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Bootstrap ────────────────────────────────────────────────────────────────
.PHONY: install
install: ## Create .venv and install all runtime + dev dependencies
	@echo "→ Creating virtual environment in $(VENV) …"
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	@echo "✓ Environment ready. Activate with: source $(VENV)/bin/activate"

.PHONY: install-prod
install-prod: ## Install runtime deps only (no dev/test tools)
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# ── Testing ──────────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run pytest with coverage (fails if coverage < 85 %)
	$(PYTEST) tests/ \
	  --cov=. \
	  --cov-report=term-missing \
	  --cov-fail-under=85 \
	  -v --tb=short

.PHONY: test-fast
test-fast: ## Run pytest without coverage (faster feedback loop)
	$(PYTEST) tests/ -v --tb=short -x

.PHONY: test-e2e
test-e2e: ## Run Playwright E2E tests against local dev servers
	cd frontend && npx playwright test

.PHONY: test-e2e-headed
test-e2e-headed: ## Run Playwright E2E tests in headed browser mode
	cd frontend && npx playwright test --headed

.PHONY: test-e2e-docker
test-e2e-docker: ## Run E2E tests via docker compose test-runner service
	docker compose --profile test run --rm test-runner

.PHONY: test-ci
test-ci: ## Same as test but outputs XML for CI artifact upload
	$(PYTEST) tests/ \
	  --cov=. \
	  --cov-report=xml \
	  --cov-report=term-missing \
	  --cov-fail-under=85 \
	  -v --tb=short

# ── Linting & formatting ─────────────────────────────────────────────────────
.PHONY: lint
lint: ## Ruff lint + format check
	$(RUFF) check .
	$(RUFF) format --check .

.PHONY: fmt
fmt: ## Auto-fix formatting with ruff
	$(RUFF) check --fix .
	$(RUFF) format .

.PHONY: typecheck
typecheck: ## Mypy static analysis
	$(MYPY) utils.py validator.py hello_world.py dashboard/api.py \
	  --ignore-missing-imports

.PHONY: check
check: lint typecheck test ## Run all quality gates (lint → typecheck → test)

# ── Docker ───────────────────────────────────────────────────────────────────
.PHONY: build
build: ## Build the production Docker image
	docker build -t hivemind:latest .

.PHONY: dev
dev: ## Start the full stack locally via docker compose
	docker compose up --build

.PHONY: down
down: ## Stop and remove containers
	docker compose down

.PHONY: logs
logs: ## Tail container logs
	docker compose logs -f bot

# ── Environment setup ────────────────────────────────────────────────────────
.PHONY: env
env: ## Copy .env.example → .env (does not overwrite existing)
	@[ -f .env ] || (cp .env.example .env && echo "✓ Created .env from .env.example")

# ── Cleanup ──────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove venv, __pycache__, and build artefacts
	rm -rf $(VENV) .coverage coverage.xml htmlcov .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

.PHONY: clean-docker
clean-docker: ## Remove dangling Docker images
	docker image prune -f
