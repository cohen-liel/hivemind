# Task Ledger

## Goal
Make the best possible version - UI, UX, and brain quality at a level that makes people switch from OpenClaw

## Complexity
EPIC

## Phases
- [x] Phase 1: Security hardening + backend reliability
- [ ] Phase 2: Frontend UX overhaul + polish
- [ ] Phase 3: Backend brain improvements (orchestration logic)
- [ ] Phase 4: Professional polish + analytics
- [ ] Phase 5: Testing + verification
- [ ] Phase 6: Final review + deployment readiness

## Current Phase
Phase 2

## Completed Work
### Phase 1 — Security Hardening + Backend Reliability
- [x] S1: SPA path traversal fix — `.resolve()` + `is_relative_to()` guard in serve_spa
- [x] S3: API key timing attack — `hmac.compare_digest()` in HTTP middleware + WebSocket auth
- [x] S5: CORS wildcard — default changed from `*` to `localhost:5173,localhost:8080`
- [x] S8: Network exposure — default host changed from `0.0.0.0` to `127.0.0.1`
- [x] S6: Unbounded recursion — cumulative `_retry_count` parameter prevents counter reset
- [x] S12: Event dict mutation — shallow copy `{**event}` before broadcasting
- [x] S13: Timezone — `ZoneInfo('Asia/Jerusalem')` for schedule matching, configurable via env
- [x] DB indexes — added `idx_messages_timestamp`, `idx_sessions_project`, `idx_activity_project`
- [x] Settings validation — cross-field check (sdk budget ≤ total budget), timeout upper bound
- [x] Blocking I/O — `asyncio.to_thread()` for `Path.iterdir()`, `read_text()`, `_read_todo()`

## Open Issues
(none currently)
