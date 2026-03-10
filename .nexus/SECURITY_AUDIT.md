# Security Audit Report — Nexus Agent OS

**Audit Date:** 2026-03-10
**Auditor:** Security Auditor Agent (task_002)
**Scope:** Full codebase security audit — API endpoints, authentication, input validation, path traversal, WebSocket security, secrets management, SQL injection, CORS configuration, dependency vulnerabilities
**Standard:** OWASP Top 10 (2021)

---

## Executive Summary

The Nexus Agent OS codebase demonstrates **good security awareness** with several proactive defenses already in place:
- Parameterized SQL queries throughout `session_manager.py`
- Column-name whitelisting for dynamic SQL in `update_project_fields()`
- Path traversal protection using `resolve()` + `is_relative_to()` checks
- Pydantic model validation on request bodies
- Rate limiting middleware with burst protection
- Request body size limits
- Optional API key authentication with constant-time comparison (`hmac.compare_digest`)
- Project ID format validation via regex
- IP address sanitization for X-Forwarded-For headers

However, there are several findings ranging from HIGH to LOW severity that should be addressed.

---

## Findings

### FINDING-01: CORS Wildcard in Production (HIGH)

**Location:** `config.py:67`, `dashboard/api.py:291-302`, `.env:CORS_ORIGINS=*`
**Severity:** HIGH
**OWASP Category:** A01 — Broken Access Control

**Description:**
The `.env` file has `CORS_ORIGINS=*` (set explicitly), and the `.env.example` also defaults to `CORS_ORIGINS=*`. While the code logs a warning when wildcard is detected, it still proceeds to configure CORS with `allow_origins=["*"]` combined with `allow_credentials=True`.

Per the CORS specification, `Access-Control-Allow-Origin: *` with `Access-Control-Allow-Credentials: true` is actually blocked by browsers. However, FastAPI's `CORSMiddleware` handles this by reflecting the requesting origin when credentials are enabled, which means **any origin can make credentialed requests**. This is a critical misconfiguration.

**Impact:**
Any website can make authenticated cross-origin requests to the dashboard API, potentially allowing an attacker to control agent sessions, send messages, or read project data if a user has the dashboard open.

**Remediation:**
1. Change `.env.example` default to `CORS_ORIGINS=http://localhost:5173,http://localhost:8080`
2. Refuse to start with `CORS_ORIGINS=*` when `DASHBOARD_API_KEY` is set (auth+wildcard CORS is contradictory)
3. Add a startup validation check that rejects wildcard CORS in production
4. Document the security implications in `.env.example`

---

### FINDING-02: Authentication is Opt-In and Off by Default (HIGH)

**Location:** `config.py:174`, `dashboard/api.py:305-314`
**Severity:** HIGH
**OWASP Category:** A07 — Identification and Authentication Failures

**Description:**
The dashboard API has **no authentication by default**. The `DASHBOARD_API_KEY` environment variable is empty, so `AUTH_ENABLED` is `False`. All API endpoints (except health/stats exemptions) are publicly accessible to anyone who can reach the server.

The endpoints allow:
- Creating/deleting projects
- Sending messages to AI agents (spending money)
- Modifying runtime settings (increasing budgets, turns)
- Browsing the filesystem
- Reading project files
- Stopping/starting agent sessions

**Impact:**
If the dashboard is exposed on a network (not just localhost), any user can fully control the agent system, spend budget, browse files, and execute agent commands.

**Remediation:**
1. Enable authentication by default — generate a random API key on first startup if none is set
2. Add a prominent startup warning when auth is disabled
3. Bind to `127.0.0.1` by default (already done — good) but add documentation warning against changing to `0.0.0.0` without enabling auth
4. Consider implementing proper user authentication (JWT/sessions) rather than a shared API key

---

### FINDING-03: No Input Validation on Numeric Query Parameters (MEDIUM)

**Location:** `dashboard/api.py:803,1542,1598,1606`
**Severity:** MEDIUM
**OWASP Category:** A03 — Injection / A04 — Insecure Design

**Description:**
Several endpoints accept numeric query parameters (`limit`, `offset`, `days`, `since`) without bounds validation:

```python
# api.py:803 — No upper bound on limit, no minimum on offset
async def get_messages(project_id: str, limit: int = 50, offset: int = 0):

# api.py:1542 — No upper bound on limit
async def get_activity(project_id: str, since: int = 0, limit: int = 200):

# api.py:1598 — No upper bound on limit
async def get_agent_recent(agent_role: str, limit: int = 10):

# api.py:1606 — No upper bound on days
async def get_cost_breakdown(project_id: str | None = None, days: int = 30):
```

**Impact:**
- A client could pass `limit=999999999` to force the server to load and serialize massive result sets, causing memory exhaustion (DoS)
- Negative values for `offset` or `limit` could cause unexpected behavior in SQL queries

**Remediation:**
Add validation for all numeric query parameters:
```python
limit = max(1, min(limit, 500))   # Clamp to reasonable bounds
offset = max(0, offset)           # Prevent negative offsets
days = max(1, min(days, 365))     # Clamp days
```

---

### FINDING-04: WebSocket Replay Lacks Input Validation (MEDIUM)

**Location:** `dashboard/api.py:1812-1841`
**Severity:** MEDIUM
**OWASP Category:** A03 — Injection

**Description:**
The WebSocket `replay` message handler accepts `project_id` and `since_sequence` from client JSON without validation:

```python
project_id = data.get("project_id", "")
since_seq = data.get("since_sequence", 0)
```

The `project_id` is not validated against the `_PROJECT_ID_RE` pattern (unlike HTTP endpoints which use `_find_manager` → `_valid_project_id`), and `since_seq` is not validated as a non-negative integer. An invalid or malicious `project_id` could potentially be passed to the session manager.

**Impact:**
Could allow accessing events from projects the user shouldn't see (though there's no per-user access control anyway — see FINDING-02). Invalid data types could cause server errors.

**Remediation:**
1. Validate `project_id` with `_valid_project_id()` before use
2. Validate `since_seq` is a non-negative integer
3. Add try/except around the database query call

---

### FINDING-05: CORS Allows All Methods and Headers (MEDIUM)

**Location:** `dashboard/api.py:296-302`
**Severity:** MEDIUM
**OWASP Category:** A05 — Security Misconfiguration

**Description:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],   # All HTTP methods
    allow_headers=["*"],   # All headers
)
```

The CORS middleware allows all HTTP methods and all headers. While this is convenient for development, in production it widens the attack surface.

**Remediation:**
Restrict to the methods and headers actually used:
```python
allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
```

---

### FINDING-06: API Key in WebSocket Query Parameter (MEDIUM)

**Location:** `dashboard/api.py:1766`
**Severity:** MEDIUM
**OWASP Category:** A02 — Cryptographic Failures

**Description:**
When authentication is enabled, the WebSocket connection requires the API key in the query string:
```python
client_key = ws.query_params.get("api_key", "")
```

Query parameters appear in:
- Server access logs
- Browser history
- Proxy logs
- Referrer headers

**Impact:**
The API key could be leaked through any of these channels, compromising authentication.

**Remediation:**
1. Use the WebSocket `Sec-WebSocket-Protocol` header for authentication tokens
2. Or implement a short-lived ticket system: client gets a one-time ticket via authenticated HTTP endpoint, then uses it for WebSocket connection
3. Or accept the key in the first WebSocket message after connection

---

### FINDING-07: CreateProjectRequest Lacks Input Sanitization (MEDIUM)

**Location:** `dashboard/api.py:91-96`
**Severity:** MEDIUM
**OWASP Category:** A03 — Injection

**Description:**
The `CreateProjectRequest` model lacks validation on several fields:
```python
class CreateProjectRequest(BaseModel):
    name: str              # No max length
    directory: str         # No max length, no format validation
    agents_count: int = 2  # No min/max bounds
    description: str = ""  # No max length
```

While the `name` is validated against `PROJECT_NAME_RE` in the endpoint handler, the `description` field has no length limit and `agents_count` has no bounds check.

**Impact:**
- Extremely long `description` or `name` values could cause storage bloat
- Negative `agents_count` could cause unexpected behavior

**Remediation:**
Add Pydantic validators:
```python
class CreateProjectRequest(BaseModel):
    name: str = Field(max_length=200)
    directory: str = Field(max_length=500)
    agents_count: int = Field(default=2, ge=1, le=10)
    description: str = Field(default="", max_length=2000)
```

---

### FINDING-08: No Rate Limiting on State-Changing Endpoints (MEDIUM)

**Location:** `dashboard/api.py:345-346`
**Severity:** MEDIUM
**OWASP Category:** A04 — Insecure Design

**Description:**
The rate limiter applies the same limits to all API endpoints. State-changing endpoints (POST/PUT/DELETE) should have stricter rate limits than read endpoints:
- POST `/api/projects/{id}/message` — sends messages to AI agents (costs money)
- PUT `/api/settings` — modifies runtime configuration
- DELETE `/api/projects/{id}` — deletes projects

The current limit is 120 requests per 60 seconds for all endpoints, which is quite generous for destructive operations.

**Remediation:**
Implement tiered rate limiting:
- GET endpoints: 120/min (current)
- POST/PUT/DELETE endpoints: 30/min
- Budget-affecting endpoints (message, settings): 10/min

---

### FINDING-09: Settings Persist Endpoint Allows Writing to Filesystem (LOW)

**Location:** `dashboard/api.py:1129-1179`
**Severity:** LOW
**OWASP Category:** A04 — Insecure Design

**Description:**
The `/api/settings/persist` endpoint writes user-provided JSON values to `data/settings_overrides.json`. While the keys are whitelisted, the values are not validated:
```python
existing.update(data)
overrides_path.write_text(json_mod.dumps(existing, indent=2))
```

A client could set values like `"max_budget_usd": 999999` or `"max_turns_per_cycle": 999999`.

**Impact:**
Potential for resource abuse through extremely permissive settings, though bounded by actual API rate limits and budget controls.

**Remediation:**
Apply the same validation ranges used in `update_settings()` to the persist endpoint.

---

### FINDING-10: Error Messages May Leak Internal Information (LOW)

**Location:** `dashboard/api.py:848,1493`
**Severity:** LOW
**OWASP Category:** A05 — Security Misconfiguration

**Description:**
Several endpoints return raw exception messages to clients:
```python
except Exception as e:
    return {"error": str(e)}  # Line 848: git command errors
    return {"error": str(e)}  # Line 1493: file tree errors
```

Exception messages can reveal:
- Internal file paths
- Database structure
- Library versions
- System configuration details

**Remediation:**
Return generic error messages to clients and log the detailed error server-side:
```python
except Exception as e:
    logger.error("Operation failed: %s", e, exc_info=True)
    return {"error": "An internal error occurred"}
```

---

### FINDING-11: No CSRF Protection Beyond CORS (LOW)

**Location:** `dashboard/api.py` (global)
**Severity:** LOW
**OWASP Category:** A01 — Broken Access Control

**Description:**
The application relies solely on CORS for CSRF protection. There are no CSRF tokens on state-changing endpoints. Since CORS is currently configured with `*`, this means there is effectively no CSRF protection.

**Impact:**
When combined with FINDING-01, any website could make cross-origin requests to state-changing endpoints.

**Remediation:**
1. Fix CORS configuration first (FINDING-01)
2. Consider adding a custom header check (e.g., require `X-Requested-With: XMLHttpRequest`) for state-changing operations as a defense-in-depth measure

---

### FINDING-12: SPA Catch-All Route Potential Path Traversal (LOW)

**Location:** `dashboard/api.py:1861-1866`
**Severity:** LOW
**OWASP Category:** A01 — Broken Access Control

**Description:**
```python
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = (frontend_dist / full_path).resolve()
    if full_path and file_path.is_relative_to(frontend_dist.resolve()) and file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(frontend_dist / "index.html")
```

The SPA serving route does use `resolve()` and `is_relative_to()` for path traversal protection, which is correct. However, the `.resolve()` call follows symlinks. If an attacker could create a symlink inside `frontend/dist`, they could escape the directory.

**Impact:**
Low — requires write access to the frontend dist directory, which would already indicate full compromise. The existing checks are adequate for normal operation.

**Remediation:**
Already adequately protected. As defense-in-depth, ensure `frontend/dist` is built in CI and not writable at runtime.

---

### FINDING-13: Dependencies Not Audited for Known CVEs (LOW)

**Location:** `requirements.txt`, `frontend/package.json`
**Severity:** LOW
**OWASP Category:** A06 — Vulnerable and Outdated Components

**Description:**
Dependencies are pinned to specific versions (good practice), but there is no evidence of regular vulnerability scanning. The dependencies appear recent (2026-03-08 pins), but no `pip audit` or `npm audit` results are present.

Key dependencies:
- `fastapi==0.135.1`
- `uvicorn==0.41.0`
- `aiosqlite==0.22.1`
- `pydantic==2.12.5`

**Remediation:**
1. Run `pip audit` and `npm audit` as part of CI
2. Add a scheduled job to check for new CVEs weekly
3. Document the audit results in the project

---

### FINDING-14: No Security Headers Middleware (LOW)

**Location:** `dashboard/api.py` (missing)
**Severity:** LOW
**OWASP Category:** A05 — Security Misconfiguration

**Description:**
The application does not set security-related HTTP headers:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`
- `Strict-Transport-Security` (if served over HTTPS)
- `X-XSS-Protection: 0` (deprecated but still useful as defense-in-depth)

**Remediation:**
Add a security headers middleware:
```python
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
```

---

### FINDING-15: Dashboard Binds to 127.0.0.1 by Default (POSITIVE)

**Location:** `server.py:307`
**Severity:** N/A (Positive Finding)

**Description:**
```python
dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
```

The server binds to localhost by default, preventing external access. This is the correct default for a development tool. However, if changed to `0.0.0.0` without enabling authentication, all API endpoints become publicly accessible.

**Remediation:**
Add a startup check: if `DASHBOARD_HOST != 127.0.0.1` and `DASHBOARD_API_KEY` is empty, refuse to start or show a prominent warning.

---

## Summary Table

| # | Severity | Finding | Location | OWASP |
|---|----------|---------|----------|-------|
| 01 | HIGH | CORS wildcard with credentials | config.py, api.py | A01 |
| 02 | HIGH | Auth disabled by default | config.py:174 | A07 |
| 03 | MEDIUM | No bounds on query parameters | api.py:803+ | A03/A04 |
| 04 | MEDIUM | WebSocket replay lacks validation | api.py:1812 | A03 |
| 05 | MEDIUM | CORS allows all methods/headers | api.py:296 | A05 |
| 06 | MEDIUM | API key in WS query parameter | api.py:1766 | A02 |
| 07 | MEDIUM | CreateProject lacks field limits | api.py:91 | A03 |
| 08 | MEDIUM | No tiered rate limiting | api.py:345 | A04 |
| 09 | LOW | Settings persist lacks value validation | api.py:1129 | A04 |
| 10 | LOW | Error messages leak internal info | api.py:848 | A05 |
| 11 | LOW | No CSRF beyond CORS | api.py (global) | A01 |
| 12 | LOW | SPA route follows symlinks | api.py:1861 | A01 |
| 13 | LOW | Dependencies not audited | requirements.txt | A06 |
| 14 | LOW | No security headers | api.py (missing) | A05 |

---

## Areas with Good Security Practices

1. **SQL Injection Prevention:** All database queries in `session_manager.py` use parameterized queries (`?` placeholders). The dynamic column name construction in `update_project_fields()` is protected by a whitelist (`_UPDATABLE_PROJECT_FIELDS`). ✅

2. **Path Traversal Protection:** Both `/api/browse-dirs` and `/api/projects/{id}/file` use `Path.resolve()` followed by `is_relative_to()` checks. The file tree endpoint also validates symlinks won't escape the project directory. ✅

3. **Input Validation:** Pydantic models validate message bodies with length limits. Project IDs are validated with regex. Project names are validated. Schedule times are format-checked. ✅

4. **Rate Limiting:** Comprehensive per-IP rate limiting with burst protection, sliding windows, and automatic cleanup of stale entries. ✅

5. **Request Size Limiting:** Body size middleware prevents oversized requests. ✅

6. **Constant-Time Comparison:** API key authentication uses `hmac.compare_digest()` to prevent timing attacks. ✅

7. **Docker Security:** Dockerfile creates a non-root user (`appuser`), resource limits are configured, and health checks are in place. ✅

8. **Logging:** Request IDs, IP sanitization, and structured logging support auditability. ✅

9. **No Hardcoded Secrets:** Grep scan found no hardcoded passwords, API keys, or tokens in the Python codebase. The `.env` file is in `.gitignore`. ✅

10. **No XSS Vectors:** Frontend does not use `dangerouslySetInnerHTML` or `innerHTML`. React's default escaping provides XSS protection. ✅

---

## Risk Assessment

**Overall Risk Score:** MEDIUM

The application is a **local development tool** that defaults to binding on localhost, which significantly reduces the attack surface. The most critical findings (CORS wildcard, no auth) are mitigated by the localhost-only default binding.

However, if deployed on a network or in Docker with port forwarding, the lack of authentication becomes a serious issue.

---

## Prioritized Remediation Plan

### Immediate (Before Next Release)
1. **FINDING-01**: Fix CORS defaults — remove `*` from `.env.example`, add startup validation
2. **FINDING-02**: Add startup warning when auth is disabled and host is not localhost
3. **FINDING-03**: Add bounds validation to all numeric query parameters

### Short-Term (Next Sprint)
4. **FINDING-04**: Validate WebSocket replay inputs
5. **FINDING-05**: Restrict CORS methods and headers
6. **FINDING-07**: Add field-level validation to CreateProjectRequest
7. **FINDING-08**: Implement tiered rate limiting
8. **FINDING-14**: Add security headers middleware

### Medium-Term (Backlog)
9. **FINDING-06**: Improve WebSocket authentication mechanism
10. **FINDING-09**: Add value validation to settings persist
11. **FINDING-10**: Sanitize error messages
12. **FINDING-13**: Set up dependency audit in CI

---

*Report generated by Security Auditor Agent — task_002*
