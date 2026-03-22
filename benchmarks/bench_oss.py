"""
Benchmark Suite: Old (heuristic/JSON/static) vs New (LLMLingua/ChromaDB/RouteLLM)

Runs head-to-head comparisons on realistic HiveMind data to measure:
1. Context Compression: heuristic vs LLMLingua (token reduction, semantic preservation)
2. Memory Search: JSON keyword matching vs ChromaDB semantic search (recall, relevance)
3. Model Routing: static assignment vs smart classifier (cost savings, accuracy)

Uses the OpenAI-compatible API (gpt-4.1-mini) to evaluate semantic preservation.
"""

import json
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Test Data ────────────────────────────────────────────────────────────

# Realistic agent context outputs (like what orch_context produces)
CONTEXT_SAMPLES = [
    {
        "name": "Backend agent — FastAPI setup",
        "text": """[backend_dev] Round 3/5
Status: IN_PROGRESS
Files changed: src/api/routes.py, src/api/models.py, src/db/connection.py, requirements.txt, Dockerfile
Output: Successfully created FastAPI application with the following endpoints:
  - POST /api/projects — Create new project with validation
  - GET /api/projects/{id} — Retrieve project details with caching
  - PUT /api/projects/{id} — Update project (partial updates supported)
  - DELETE /api/projects/{id} — Soft delete with cascade
  - GET /api/health — Health check endpoint
  
  Database layer uses SQLAlchemy async with PostgreSQL driver (asyncpg).
  Connection pooling configured with pool_size=5, max_overflow=10.
  Alembic migrations initialized with initial schema.
  
  Docker configuration updated:
  - Multi-stage build (builder + runtime)
  - Non-root user for security
  - Health check probe on /api/health
  
  Dependencies added: fastapi==0.135.1, uvicorn[standard]==0.41.0, 
  sqlalchemy[asyncio]>=2.0.36, asyncpg>=0.30.0, alembic>=1.13.0
  
  Test results: 12/12 passed (unit tests for all CRUD operations)
  Coverage: 87% on src/api/, 92% on src/db/
  
  Known issues:
  - Need to add rate limiting middleware
  - CORS configuration pending frontend URL
  - WebSocket support not yet implemented
  
Commands: pip install -r requirements.txt, alembic upgrade head, uvicorn src.api.main:app --reload
Diff summary: +847 lines, -12 lines across 5 files
Issues: Rate limiting not implemented, CORS needs frontend URL, WebSocket pending"""
    },
    {
        "name": "Frontend agent — React dashboard",
        "text": """[frontend_dev] Round 2/4
Status: COMPLETED
Files changed: frontend/src/App.tsx, frontend/src/components/Dashboard.tsx, frontend/src/components/ProjectList.tsx, frontend/src/hooks/useProjects.ts, frontend/src/api/client.ts, frontend/package.json, frontend/tailwind.config.js
Output: Built complete React dashboard with TypeScript and TailwindCSS:

  Component hierarchy:
  App.tsx
  ├── Header (navigation, user menu)
  ├── Dashboard
  │   ├── StatsCards (project count, active tasks, completion rate)
  │   ├── ProjectList
  │   │   └── ProjectCard (status badge, progress bar, actions)
  │   └── ActivityFeed (recent events timeline)
  └── Footer

  State management: React Query (TanStack Query v5) for server state,
  Zustand for UI state (sidebar open/close, theme preference).
  
  API client: Axios with interceptors for auth token injection,
  automatic retry on 5xx errors, request/response logging in dev mode.
  
  Styling: TailwindCSS with custom design tokens matching brand guidelines.
  Dark mode support via CSS variables and Tailwind dark: prefix.
  Responsive breakpoints: sm (640px), md (768px), lg (1024px), xl (1280px).
  
  Accessibility: All interactive elements have aria-labels,
  keyboard navigation support, focus management for modals.
  Color contrast ratios meet WCAG 2.1 AA standards.
  
  Bundle analysis: 
  - Total: 142KB gzipped (target was <200KB)
  - React + ReactDOM: 42KB
  - TanStack Query: 12KB
  - Application code: 88KB
  
  Test results: 28/28 passed (component tests with React Testing Library)
  Lighthouse scores: Performance 96, Accessibility 100, Best Practices 95, SEO 92
  
Commands: npm install, npm run build, npm run test
Diff summary: +1,247 lines across 7 files"""
    },
    {
        "name": "DevOps agent — CI/CD pipeline",
        "text": """[devops] Round 1/3
Status: IN_PROGRESS
Files changed: .github/workflows/ci.yml, .github/workflows/deploy.yml, docker-compose.prod.yml, scripts/deploy.sh, terraform/main.tf, terraform/variables.tf
Output: Set up comprehensive CI/CD pipeline with GitHub Actions:

  CI Pipeline (.github/workflows/ci.yml):
  - Trigger: push to main, pull requests
  - Jobs: lint, test, build, security-scan
  - Lint: ESLint + Prettier (frontend), Ruff + Black (backend)
  - Test: pytest with coverage (backend), vitest (frontend)
  - Build: Docker multi-stage build with layer caching
  - Security: Snyk vulnerability scan, Trivy container scan
  - Artifacts: Test reports, coverage badges, Docker image
  
  Deploy Pipeline (.github/workflows/deploy.yml):
  - Trigger: tag push (v*)
  - Stages: staging → production (manual approval gate)
  - Infrastructure: Terraform for AWS ECS Fargate
  - Blue/green deployment with ALB target group switching
  - Rollback: automatic on health check failure (3 consecutive)
  - Notifications: Slack webhook on deploy success/failure
  
  Terraform configuration:
  - VPC with public/private subnets across 3 AZs
  - ECS Fargate cluster with auto-scaling (min 2, max 10)
  - RDS PostgreSQL (db.t3.medium, Multi-AZ, encrypted)
  - ElastiCache Redis for session storage
  - CloudFront CDN for frontend static assets
  - ACM certificate for HTTPS
  - WAF rules for common attack patterns
  
  Estimated monthly cost: $180-$350 depending on traffic
  
Issues: Need AWS credentials in GitHub Secrets, Terraform state backend not configured
Commands: terraform init, terraform plan, terraform apply
Test results: CI pipeline dry-run passed, deploy pipeline needs secrets"""
    },
    {
        "name": "Security agent — audit report",
        "text": """[security] Round 1/1
Status: COMPLETED
Files changed: docs/security-audit.md, src/middleware/rate_limit.py, src/middleware/cors.py, src/auth/jwt.py
Output: Completed security audit and implemented critical fixes:

  CRITICAL FINDINGS:
  1. SQL Injection vulnerability in project search endpoint
     - Raw string interpolation in SQLAlchemy query
     - Fix: Parameterized queries with bound parameters
     - Severity: CRITICAL (CVSS 9.8)
  
  2. Missing rate limiting on authentication endpoints
     - Brute force attack possible on /api/auth/login
     - Fix: Implemented sliding window rate limiter (10 req/min)
     - Severity: HIGH (CVSS 7.5)
  
  3. JWT token not invalidated on password change
     - Old tokens remain valid after password reset
     - Fix: Added token version claim, increment on password change
     - Severity: MEDIUM (CVSS 5.3)
  
  MODERATE FINDINGS:
  4. CORS misconfiguration allowing wildcard origin
     - Fix: Restricted to specific frontend domains
  5. Missing Content-Security-Policy headers
     - Fix: Added CSP with nonce-based script allowlist
  6. Session cookies missing Secure and SameSite flags
     - Fix: Set Secure=True, SameSite=Strict, HttpOnly=True
  
  LOW FINDINGS:
  7. Server version exposed in response headers
  8. Missing X-Frame-Options header
  9. Debug mode enabled in production config
  
  All critical and high findings have been fixed and verified.
  Penetration test re-run: 0 critical, 0 high, 2 medium (accepted risk), 3 low
  
  Recommendations:
  - Implement API key rotation policy (every 90 days)
  - Add request signing for inter-service communication
  - Enable audit logging for all authentication events
  - Consider implementing mTLS for internal services
  
Test results: Security regression tests 15/15 passed
Diff summary: +312 lines, -45 lines across 4 files"""
    },
    {
        "name": "Architect agent — system design",
        "text": """[architect] Round 1/2
Status: IN_PROGRESS
Files changed: docs/architecture.md, docs/adr/001-database-choice.md, docs/adr/002-auth-strategy.md
Output: Produced system architecture document and key Architecture Decision Records:

  System Overview:
  The platform follows a modular monolith architecture with clear bounded contexts,
  designed for eventual extraction into microservices if scale demands it.
  
  Core Modules:
  1. Project Management — CRUD, lifecycle, permissions
  2. Agent Orchestration — DAG execution, task scheduling, context management
  3. User Authentication — OAuth2/JWT, role-based access control
  4. Real-time Communication — WebSocket hub for live updates
  5. File Storage — S3-compatible object storage with CDN
  6. Analytics — Event sourcing for audit trail, metrics aggregation
  
  Technology Stack Decision:
  - Backend: Python 3.11 + FastAPI (async-first, type-safe)
  - Database: PostgreSQL 15 (JSONB for flexible schemas, full-text search)
  - Cache: Redis 7 (session store, pub/sub for WebSocket fan-out)
  - Queue: Celery + Redis (background tasks, scheduled jobs)
  - Frontend: React 18 + TypeScript + TailwindCSS
  - Infrastructure: AWS ECS Fargate (serverless containers)
  
  ADR-001: PostgreSQL over MongoDB
  - Decision: Use PostgreSQL with JSONB columns for semi-structured data
  - Rationale: ACID compliance critical for project state, JSONB provides
    document-store flexibility, mature ecosystem, better tooling
  - Rejected: MongoDB (eventual consistency risks), DynamoDB (vendor lock-in)
  
  ADR-002: JWT + OAuth2 over Session-based auth
  - Decision: Stateless JWT with short-lived access tokens (15min) + refresh tokens
  - Rationale: Horizontal scalability, no server-side session storage needed,
    compatible with mobile clients and third-party integrations
  - Rejected: Server-side sessions (scaling complexity), API keys only (no user context)
  
  Scalability targets:
  - 10,000 concurrent users
  - 100 requests/second sustained
  - 99.9% uptime SLA
  - <200ms p95 API response time
  
Issues: Need to finalize WebSocket scaling strategy (Redis pub/sub vs dedicated broker)
Commands: N/A (documentation only)"""
    },
]

# Memory lessons for testing semantic search vs keyword matching
MEMORY_LESSONS = [
    {"project_id": "proj_1", "category": "configuration", "lesson": "FastAPI with Docker requires specific Dockerfile configuration — use multi-stage builds with python:3.11-slim as base, install system deps in builder stage, copy only compiled packages to runtime stage", "tech_stack": ["fastapi", "docker", "python"], "severity": "info"},
    {"project_id": "proj_1", "category": "failure", "lesson": "PostgreSQL connection pool exhaustion under load — set pool_size=5 and max_overflow=10 in SQLAlchemy, add connection recycling with pool_recycle=3600", "tech_stack": ["postgresql", "sqlalchemy", "python"], "severity": "warning"},
    {"project_id": "proj_2", "category": "configuration", "lesson": "React build fails in CI when using TailwindCSS JIT mode — need to set NODE_ENV=production and ensure content paths in tailwind.config.js include all component directories", "tech_stack": ["react", "tailwindcss", "ci"], "severity": "info"},
    {"project_id": "proj_2", "category": "failure", "lesson": "WebSocket connections drop after 60 seconds on AWS ALB — configure idle timeout to 3600 and enable sticky sessions for WebSocket target group", "tech_stack": ["websocket", "aws", "alb"], "severity": "warning"},
    {"project_id": "proj_3", "category": "security", "lesson": "JWT tokens must be invalidated on password change — add a token_version claim and increment it in the user table on every password reset", "tech_stack": ["jwt", "authentication", "security"], "severity": "critical"},
    {"project_id": "proj_3", "category": "configuration", "lesson": "Terraform state must be stored in S3 with DynamoDB locking — never use local state in team environments, enable versioning on the S3 bucket", "tech_stack": ["terraform", "aws", "infrastructure"], "severity": "info"},
    {"project_id": "proj_4", "category": "performance", "lesson": "Redis pub/sub fan-out bottleneck with 1000+ WebSocket connections — switch to Redis Streams with consumer groups for better backpressure handling", "tech_stack": ["redis", "websocket", "performance"], "severity": "warning"},
    {"project_id": "proj_4", "category": "failure", "lesson": "Alembic migration conflicts when multiple developers work on schema changes — use a linear migration chain and squash migrations before merging to main", "tech_stack": ["alembic", "postgresql", "migrations"], "severity": "info"},
    {"project_id": "proj_5", "category": "configuration", "lesson": "GitHub Actions CI cache invalidation issue — use hashFiles('**/requirements.txt') as cache key, not a static string, to ensure deps are rebuilt when requirements change", "tech_stack": ["github-actions", "ci", "python"], "severity": "info"},
    {"project_id": "proj_5", "category": "security", "lesson": "CORS wildcard origin allows credential theft via cross-site requests — always specify exact allowed origins, never use '*' when credentials are involved", "tech_stack": ["cors", "security", "web"], "severity": "critical"},
    {"project_id": "proj_6", "category": "performance", "lesson": "Container startup time exceeds 30 seconds causing ECS health check failures — optimize Dockerfile layer ordering, pre-compile Python bytecode, use slim base image", "tech_stack": ["docker", "ecs", "performance"], "severity": "warning"},
    {"project_id": "proj_6", "category": "configuration", "lesson": "Next.js ISR revalidation not working behind CloudFront — set Cache-Control headers correctly and configure CloudFront to respect origin cache headers", "tech_stack": ["nextjs", "cloudfront", "caching"], "severity": "info"},
]

# Semantic search queries — designed to test whether the system can find relevant
# lessons WITHOUT exact keyword matches
SEMANTIC_QUERIES = [
    {"query": "container networking problems", "expected_lessons": [0, 3, 10], "description": "Should find Docker/container-related lessons even though 'networking' isn't in them"},
    {"query": "database connection issues under heavy traffic", "expected_lessons": [1, 6], "description": "Should find pool exhaustion and Redis bottleneck lessons"},
    {"query": "how to secure API endpoints", "expected_lessons": [4, 9], "description": "Should find JWT and CORS security lessons"},
    {"query": "deployment pipeline keeps failing", "expected_lessons": [2, 8, 10], "description": "Should find CI/CD and build-related lessons"},
    {"query": "real-time updates dropping", "expected_lessons": [3, 6], "description": "Should find WebSocket-related lessons"},
    {"query": "infrastructure as code best practices", "expected_lessons": [5], "description": "Should find Terraform state management lesson"},
    {"query": "frontend build optimization", "expected_lessons": [2, 10, 11], "description": "Should find build/performance related lessons"},
    {"query": "authentication token management", "expected_lessons": [4], "description": "Should find JWT invalidation lesson"},
]

# Routing test cases — prompts with known expected complexity
ROUTING_CASES = [
    {"role": "backend", "prompt": "Fix a typo in the README file", "expected": "weak", "reason": "Simple text fix"},
    {"role": "backend", "prompt": "Add a comment to the config file explaining the database URL format", "expected": "weak", "reason": "Documentation task"},
    {"role": "formatter", "prompt": "Format all Python files with Black and fix linting issues", "expected": "weak", "reason": "Mechanical formatting"},
    {"role": "backend", "prompt": "Rename the variable 'x' to 'project_count' in stats.py", "expected": "weak", "reason": "Simple rename"},
    {"role": "backend", "prompt": "Update the numpy dependency from 1.24 to 1.26", "expected": "weak", "reason": "Version bump"},
    {"role": "architect", "prompt": "Design the microservices architecture for the payment processing system with event sourcing, CQRS, and saga pattern for distributed transactions", "expected": "strong", "reason": "Complex architecture design"},
    {"role": "backend", "prompt": "Implement a complete OAuth2 authentication flow with JWT tokens, refresh token rotation, and role-based access control", "expected": "strong", "reason": "Complex feature implementation"},
    {"role": "security", "prompt": "Perform a comprehensive security audit of the API endpoints, check for SQL injection, XSS, CSRF, and implement rate limiting", "expected": "strong", "reason": "Security audit"},
    {"role": "devops", "prompt": "Set up a blue/green deployment pipeline with automatic rollback, canary releases, and infrastructure as code using Terraform", "expected": "strong", "reason": "Complex DevOps setup"},
    {"role": "tech_lead", "prompt": "Review the entire codebase for performance bottlenecks, refactor the database layer, and implement caching strategy", "expected": "strong", "reason": "Complex refactoring"},
    {"role": "backend", "prompt": "Create a new REST API endpoint for user profile management with CRUD operations, input validation, and database integration", "expected": "strong", "reason": "Feature implementation from scratch"},
    {"role": "pm", "prompt": "Break down the project requirements into technical tasks, estimate effort, and create the sprint plan", "expected": "strong", "reason": "PM planning (always-strong role)"},
    {"role": "backend", "prompt": "Debug the complex race condition in the concurrent task executor that causes intermittent data corruption", "expected": "strong", "reason": "Complex debugging"},
    {"role": "documenter", "prompt": "Update the API documentation with the new endpoint descriptions", "expected": "weak", "reason": "Documentation update"},
    {"role": "backend", "prompt": "Remove unused imports from all Python files in the src directory", "expected": "weak", "reason": "Cleanup task"},
]


def run_compression_benchmark():
    """Benchmark 1: Heuristic vs LLMLingua context compression."""
    print("\n" + "=" * 70)
    print("BENCHMARK 1: Context Compression — Heuristic vs LLMLingua")
    print("=" * 70)

    from context_compressor import (
        _heuristic_compress,
        compress_text_llmlingua,
        compress_context_smart,
        _get_compressor,
    )

    # Force-load the compressor
    print("\n[*] Loading LLMLingua model (first time may download ~400MB)...")
    compressor = _get_compressor()
    if compressor is None:
        print("[!] LLMLingua not available — skipping LLMLingua benchmarks")
        llmlingua_available = False
    else:
        print("[✓] LLMLingua loaded successfully")
        llmlingua_available = True

    results = []

    for sample in CONTEXT_SAMPLES:
        original = sample["text"]
        original_tokens = len(original.split())

        # Heuristic compression
        t0 = time.perf_counter()
        heuristic_result = _heuristic_compress(original)
        heuristic_time = time.perf_counter() - t0
        heuristic_tokens = len(heuristic_result.split())

        result = {
            "name": sample["name"],
            "original_tokens": original_tokens,
            "heuristic_tokens": heuristic_tokens,
            "heuristic_ratio": round(heuristic_tokens / original_tokens, 3),
            "heuristic_time_ms": round(heuristic_time * 1000, 2),
        }

        # LLMLingua compression
        if llmlingua_available:
            t0 = time.perf_counter()
            llm_result = compress_text_llmlingua(original, target_ratio=0.5)
            llm_time = time.perf_counter() - t0

            if llm_result:
                llm_tokens = len(llm_result.split())
                result["llmlingua_tokens"] = llm_tokens
                result["llmlingua_ratio"] = round(llm_tokens / original_tokens, 3)
                result["llmlingua_time_ms"] = round(llm_time * 1000, 2)
            else:
                result["llmlingua_tokens"] = "FAILED"
                result["llmlingua_ratio"] = "N/A"
                result["llmlingua_time_ms"] = "N/A"

        results.append(result)

    # Print results table
    print(f"\n{'Sample':<40} {'Original':>8} {'Heuristic':>10} {'H-Ratio':>8} {'H-Time':>8}", end="")
    if llmlingua_available:
        print(f" {'LLMLingua':>10} {'L-Ratio':>8} {'L-Time':>8}", end="")
    print()
    print("-" * (90 if llmlingua_available else 70))

    for r in results:
        print(f"{r['name']:<40} {r['original_tokens']:>8} {r['heuristic_tokens']:>10} {r['heuristic_ratio']:>8} {r['heuristic_time_ms']:>7}ms", end="")
        if llmlingua_available:
            lt = r.get('llmlingua_tokens', 'N/A')
            lr = r.get('llmlingua_ratio', 'N/A')
            ltm = r.get('llmlingua_time_ms', 'N/A')
            print(f" {lt:>10} {lr:>8} {ltm:>7}ms" if isinstance(lt, int) else f" {lt:>10} {lr:>8} {ltm:>8}", end="")
        print()

    # Semantic preservation test using LLM
    if llmlingua_available:
        print("\n[*] Testing semantic preservation with LLM evaluation...")
        _evaluate_semantic_preservation(results, CONTEXT_SAMPLES)

    return results


def _evaluate_semantic_preservation(results, samples):
    """Use an LLM to evaluate whether compressed text preserves key information."""
    try:
        from openai import OpenAI
        client = OpenAI()
    except Exception as e:
        print(f"[!] Cannot evaluate semantic preservation: {e}")
        return

    from context_compressor import _heuristic_compress, compress_text_llmlingua

    scores = {"heuristic": [], "llmlingua": []}

    for sample in samples[:3]:  # Test first 3 samples to save API calls
        original = sample["text"]
        heuristic = _heuristic_compress(original)
        llmlingua = compress_text_llmlingua(original, target_ratio=0.5) or ""

        for method, compressed in [("heuristic", heuristic), ("llmlingua", llmlingua)]:
            if not compressed:
                continue
            try:
                response = client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[{
                        "role": "user",
                        "content": f"""Rate how well the compressed text preserves the KEY INFORMATION from the original.

ORIGINAL:
{original[:2000]}

COMPRESSED ({method}):
{compressed[:2000]}

Score from 1-10 where:
1 = Almost all information lost
5 = Key facts preserved but details lost
10 = All important information preserved

Reply with ONLY a JSON object: {{"score": <number>, "preserved": ["list of key facts preserved"], "lost": ["list of key facts lost"]}}"""
                    }],
                    temperature=0.1,
                )
                content = response.choices[0].message.content.strip()
                # Try to parse JSON
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                data = json.loads(content)
                scores[method].append(data.get("score", 0))
                print(f"  [{method}] {sample['name'][:30]}: score={data.get('score', '?')}/10")
            except Exception as e:
                print(f"  [{method}] {sample['name'][:30]}: evaluation failed — {e}")

    print("\n--- Semantic Preservation Summary ---")
    for method in ["heuristic", "llmlingua"]:
        if scores[method]:
            avg = sum(scores[method]) / len(scores[method])
            print(f"  {method:>12}: avg score = {avg:.1f}/10 (n={len(scores[method])})")


def run_memory_benchmark():
    """Benchmark 2: JSON keyword matching vs ChromaDB semantic search."""
    print("\n" + "=" * 70)
    print("BENCHMARK 2: Memory Search — JSON Keyword vs ChromaDB Semantic")
    print("=" * 70)

    # Create two separate memory stores
    tmp_dir = tempfile.mkdtemp(prefix="hivemind_bench_")
    json_dir = os.path.join(tmp_dir, "json_store")
    chroma_dir = os.path.join(tmp_dir, "chroma_store")

    try:
        # Initialize JSON-only store
        os.environ["MEMORY_BACKEND"] = "json"
        # Need to reimport to pick up env change
        import importlib
        import cross_project_memory as cpm
        importlib.reload(cpm)
        json_mem = cpm.CrossProjectMemory(json_dir)

        # Initialize ChromaDB store
        os.environ["MEMORY_BACKEND"] = "chroma"
        importlib.reload(cpm)
        chroma_mem = cpm.CrossProjectMemory(chroma_dir)

        # Populate both stores with the same lessons
        print(f"\n[*] Populating both stores with {len(MEMORY_LESSONS)} lessons...")
        for lesson in MEMORY_LESSONS:
            json_mem.add_lesson(**lesson)
            chroma_mem.add_lesson(**lesson)

        print(f"  JSON store: {json_mem.stats}")
        print(f"  ChromaDB store: {chroma_mem.stats}")

        # Run semantic queries
        print(f"\n[*] Running {len(SEMANTIC_QUERIES)} semantic search queries...\n")

        results = []
        json_total_recall = 0
        chroma_total_recall = 0

        for sq in SEMANTIC_QUERIES:
            query = sq["query"]
            expected_indices = set(sq["expected_lessons"])
            expected_lessons = {MEMORY_LESSONS[i]["lesson"][:80] for i in expected_indices}

            # JSON keyword search
            t0 = time.perf_counter()
            json_results = json_mem.get_lessons(query=query, limit=5)
            json_time = time.perf_counter() - t0
            json_found = {r["lesson"][:80] for r in json_results}
            json_hits = len(json_found & expected_lessons)
            json_recall = json_hits / len(expected_lessons) if expected_lessons else 0

            # ChromaDB semantic search
            t0 = time.perf_counter()
            chroma_results = chroma_mem.get_lessons(query=query, limit=5)
            chroma_time = time.perf_counter() - t0
            chroma_found = {r["lesson"][:80] for r in chroma_results}
            chroma_hits = len(chroma_found & expected_lessons)
            chroma_recall = chroma_hits / len(expected_lessons) if expected_lessons else 0

            json_total_recall += json_recall
            chroma_total_recall += chroma_recall

            result = {
                "query": query,
                "expected": len(expected_lessons),
                "json_hits": json_hits,
                "json_recall": round(json_recall, 2),
                "json_time_ms": round(json_time * 1000, 2),
                "chroma_hits": chroma_hits,
                "chroma_recall": round(chroma_recall, 2),
                "chroma_time_ms": round(chroma_time * 1000, 2),
            }
            results.append(result)

            winner = "ChromaDB" if chroma_recall > json_recall else ("TIE" if chroma_recall == json_recall else "JSON")
            print(f"  Query: \"{query}\"")
            print(f"    Expected: {len(expected_lessons)} | JSON: {json_hits}/{len(expected_lessons)} ({json_recall:.0%}) | ChromaDB: {chroma_hits}/{len(expected_lessons)} ({chroma_recall:.0%}) → {winner}")

        # Summary
        n = len(SEMANTIC_QUERIES)
        print(f"\n--- Memory Search Summary ---")
        print(f"  JSON avg recall:    {json_total_recall/n:.1%}")
        print(f"  ChromaDB avg recall: {chroma_total_recall/n:.1%}")
        print(f"  Improvement:         {((chroma_total_recall - json_total_recall) / max(json_total_recall, 0.01)) * 100:+.0f}%")

        return results

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_routing_benchmark():
    """Benchmark 3: Static assignment vs Smart Router."""
    print("\n" + "=" * 70)
    print("BENCHMARK 3: Model Routing — Static vs Smart Classifier")
    print("=" * 70)

    from smart_router import _builtin_classify, route_model_for_task, STRONG_MODEL, WEAK_MODEL, ALWAYS_STRONG_ROLES

    correct_builtin = 0
    total = len(ROUTING_CASES)
    strong_count = 0
    weak_count = 0

    print(f"\n{'Role':<15} {'Expected':>8} {'Got':>8} {'Score':>6} {'Correct':>8}  Reason")
    print("-" * 95)

    for case in ROUTING_CASES:
        complexity = _builtin_classify(case["prompt"], case["role"])
        model = route_model_for_task(case["role"], case["prompt"])
        got = "strong" if model == STRONG_MODEL else "weak"
        correct = got == case["expected"]
        if correct:
            correct_builtin += 1
        if got == "strong":
            strong_count += 1
        else:
            weak_count += 1

        mark = "✓" if correct else "✗"
        print(f"{case['role']:<15} {case['expected']:>8} {got:>8} {complexity:>6.2f} {mark:>8}  {case['reason']}")

    accuracy = correct_builtin / total
    print(f"\n--- Routing Summary ---")
    print(f"  Accuracy:      {accuracy:.1%} ({correct_builtin}/{total})")
    print(f"  Strong model:  {strong_count}/{total} ({strong_count/total:.0%})")
    print(f"  Weak model:    {weak_count}/{total} ({weak_count/total:.0%})")

    # Cost savings estimate
    from smart_router import estimate_cost_savings
    savings = estimate_cost_savings(total, strong_count / total)
    all_strong = estimate_cost_savings(total, 1.0)
    print(f"  Cost (all strong): ${all_strong['estimated_cost']:.2f}")
    print(f"  Cost (routed):     ${savings['estimated_cost']:.2f}")
    print(f"  Savings:           ${savings['estimated_savings']:.2f} ({savings['savings_percent']:.0f}%)")

    return {
        "accuracy": accuracy,
        "strong_ratio": strong_count / total,
        "weak_ratio": weak_count / total,
        "savings": savings,
    }


def main():
    """Run all benchmarks and save results."""
    print("=" * 70)
    print("HiveMind OSS Outsourcing — Performance Benchmark Suite")
    print("=" * 70)
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version.split()[0]}")

    all_results = {}

    # Benchmark 1: Context Compression
    try:
        all_results["compression"] = run_compression_benchmark()
    except Exception as e:
        print(f"\n[ERROR] Compression benchmark failed: {e}")
        import traceback
        traceback.print_exc()

    # Benchmark 2: Memory Search
    try:
        all_results["memory"] = run_memory_benchmark()
    except Exception as e:
        print(f"\n[ERROR] Memory benchmark failed: {e}")
        import traceback
        traceback.print_exc()

    # Benchmark 3: Model Routing
    try:
        all_results["routing"] = run_routing_benchmark()
    except Exception as e:
        print(f"\n[ERROR] Routing benchmark failed: {e}")
        import traceback
        traceback.print_exc()

    # Save raw results
    results_file = Path(__file__).parent / "benchmark_results.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n[*] Raw results saved to {results_file}")

    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
