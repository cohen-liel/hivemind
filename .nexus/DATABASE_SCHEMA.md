# Database Schema Documentation

> **Engine:** SQLite 3 with WAL mode
> **File:** `data/sessions.db`
> **ORM/Driver:** `aiosqlite` (async wrapper)
> **Connection pooling:** `ConnectionPool` (custom, configurable via `DB_MAX_CONNECTIONS`)

---

## Schema Version Tracking

Migrations are tracked in `_schema_versions`. Each migration has a numeric
version, a human-readable name, and a timestamp of when it was applied.

| Version | Name | Description |
|---------|------|-------------|
| 1 | `add_session_id_to_messages` | Adds nullable `session_id` column to `messages` |
| 2 | `add_next_run_to_schedules` | Adds nullable `next_run` (REAL) column to `schedules` |
| 3 | `add_performance_indexes` | Creates indexes on `messages(session_id, timestamp)`, `schedules(next_run)`, `messages(project_id, agent_name, timestamp)` |

---

## Tables

### `projects`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| project_id | TEXT | PRIMARY KEY | Slug (e.g. `web-claude-bot`) |
| user_id | INTEGER | NOT NULL | Owner user ID |
| name | TEXT | NOT NULL | Display name |
| description | TEXT | DEFAULT '' | |
| project_dir | TEXT | NOT NULL | Absolute filesystem path |
| status | TEXT | DEFAULT 'active' | active / archived |
| away_mode | INTEGER | DEFAULT 0 | 0=off, 1=on |
| budget_usd | REAL | DEFAULT 0 | 0 = unlimited |
| message_count | INTEGER | DEFAULT 0 | Maintained by triggers |
| created_at | REAL | NOT NULL | Unix epoch |
| updated_at | REAL | NOT NULL | Unix epoch |

### `sessions`

Tracks Claude SDK sessions for resume support.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| user_id | INTEGER | NOT NULL |
| agent_role | TEXT | NOT NULL |
| session_id | TEXT | NOT NULL |
| cost_usd | REAL | DEFAULT 0.0 |
| turns | INTEGER | DEFAULT 0 |
| status | TEXT | DEFAULT 'active' |
| created_at | REAL | NOT NULL |
| updated_at | REAL | NOT NULL |

**Unique constraint:** `(project_id, user_id, agent_role)`

### `messages`

Conversation history log.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| agent_name | TEXT | NOT NULL |
| role | TEXT | NOT NULL |
| content | TEXT | NOT NULL |
| cost_usd | REAL | DEFAULT 0.0 |
| timestamp | REAL | NOT NULL |
| session_id | TEXT | DEFAULT NULL | *(added by migration 1)* |

### `notification_prefs`

| Column | Type | Constraints |
|--------|------|-------------|
| user_id | INTEGER | PRIMARY KEY |
| level | TEXT | DEFAULT 'all' |
| budget_warning | INTEGER | DEFAULT 1 |
| stall_alert | INTEGER | DEFAULT 1 |
| updated_at | REAL | NOT NULL |

### `task_history`

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| user_id | INTEGER | NOT NULL |
| task_description | TEXT | NOT NULL |
| status | TEXT | DEFAULT 'running' |
| cost_usd | REAL | DEFAULT 0.0 |
| turns_used | INTEGER | DEFAULT 0 |
| started_at | REAL | NOT NULL |
| completed_at | REAL | |
| summary | TEXT | DEFAULT '' |

### `away_digest`

Events queued while user is in away mode.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | INTEGER | NOT NULL |
| project_id | TEXT | NOT NULL |
| event_type | TEXT | NOT NULL |
| summary | TEXT | NOT NULL |
| timestamp | REAL | NOT NULL |

### `schedules`

Recurring / one-time scheduled tasks.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | INTEGER | NOT NULL |
| project_id | TEXT | NOT NULL |
| schedule_time | TEXT | NOT NULL | HH:MM format |
| task_description | TEXT | NOT NULL |
| repeat | TEXT | DEFAULT 'once' | once / daily / weekly |
| enabled | INTEGER | DEFAULT 1 |
| last_run | REAL | |
| next_run | REAL | DEFAULT NULL | *(added by migration 2)* |
| created_at | REAL | NOT NULL |

### `lessons`

Experience memory for cross-task learning.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| user_id | INTEGER | NOT NULL |
| task_description | TEXT | NOT NULL |
| lesson_type | TEXT | DEFAULT 'general' |
| lesson | TEXT | NOT NULL |
| tags | TEXT | DEFAULT '' |
| outcome | TEXT | DEFAULT 'success' |
| rounds_used | INTEGER | DEFAULT 0 |
| cost_usd | REAL | DEFAULT 0.0 |
| created_at | REAL | NOT NULL |

### `activity_log`

Cross-device sync event stream.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| sequence_id | INTEGER | NOT NULL DEFAULT 0 |
| event_type | TEXT | NOT NULL |
| agent | TEXT | DEFAULT '' |
| data | TEXT | DEFAULT '{}' | JSON blob |
| timestamp | REAL | NOT NULL |

### `agent_performance`

Execution metrics per agent invocation.

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| project_id | TEXT | NOT NULL |
| agent_role | TEXT | NOT NULL |
| task_description | TEXT | DEFAULT '' |
| status | TEXT | DEFAULT 'success' |
| duration_seconds | REAL | DEFAULT 0.0 |
| cost_usd | REAL | DEFAULT 0.0 |
| turns_used | INTEGER | DEFAULT 0 |
| error_message | TEXT | DEFAULT '' |
| round_number | INTEGER | DEFAULT 0 |
| created_at | REAL | NOT NULL |

### `orchestrator_state`

Crash-recovery state for the orchestrator.

| Column | Type | Constraints |
|--------|------|-------------|
| project_id | TEXT | PRIMARY KEY |
| user_id | INTEGER | NOT NULL |
| status | TEXT | DEFAULT 'idle' |
| current_loop | INTEGER | DEFAULT 0 |
| turn_count | INTEGER | DEFAULT 0 |
| total_cost_usd | REAL | DEFAULT 0.0 |
| shared_context | TEXT | DEFAULT '[]' | JSON |
| agent_states | TEXT | DEFAULT '{}' | JSON |
| last_user_message | TEXT | DEFAULT '' |
| updated_at | REAL | NOT NULL |

### `_schema_versions`

Migration tracking (system table).

| Column | Type | Constraints |
|--------|------|-------------|
| version | INTEGER | PRIMARY KEY |
| name | TEXT | NOT NULL |
| applied_at | REAL | NOT NULL |

### `_db_metadata`

Key-value store for DB maintenance metadata (e.g. last VACUUM timestamp).

| Column | Type | Constraints |
|--------|------|-------------|
| key | TEXT | PRIMARY KEY |
| value | TEXT | NOT NULL |

---

## Indexes

| Index | Table | Columns | Notes |
|-------|-------|---------|-------|
| idx_sessions_lookup | sessions | (project_id, user_id, agent_role) | Session resume |
| idx_sessions_project | sessions | (project_id) | Project listing |
| idx_messages_project | messages | (project_id, timestamp) | Recent messages |
| idx_messages_timestamp | messages | (timestamp) | Cleanup queries |
| idx_messages_session_ts | messages | (session_id, timestamp) | *Migration 3* |
| idx_messages_agent_ts | messages | (project_id, agent_name, timestamp) | *Migration 3* |
| idx_task_history_project | task_history | (project_id, completed_at) | Task listing |
| idx_away_digest_user | away_digest | (user_id, timestamp) | Digest retrieval |
| idx_schedules_enabled | schedules | (enabled, schedule_time) | Due schedule lookup |
| idx_schedules_next_run | schedules | (next_run) | *Migration 3* |
| idx_lessons_project | lessons | (project_id, created_at) | Project lessons |
| idx_lessons_user | lessons | (user_id, created_at) | Cross-project lessons |
| idx_activity_project_seq | activity_log | (project_id, sequence_id) | Sync catchup |
| idx_activity_project_ts | activity_log | (project_id, timestamp) | Time-range queries |
| idx_agent_perf_project | agent_performance | (project_id, agent_role, created_at) | Performance stats |
| idx_agent_perf_role | agent_performance | (agent_role, status) | Role-level stats |

---

## Triggers

| Trigger | Event | Purpose |
|---------|-------|---------|
| trg_messages_insert_count | AFTER INSERT ON messages | Increment `projects.message_count` |
| trg_messages_delete_count | AFTER DELETE ON messages | Decrement `projects.message_count` |

---

## Connection Pooling

The `ConnectionPool` class manages multiple `aiosqlite` connections:

- **Max connections:** Configurable via `DB_MAX_CONNECTIONS` (default 5)
- **Lazy creation:** Connections created on-demand up to the limit
- **Health checks:** Each acquired connection is verified with `SELECT 1`
- **WAL mode:** All connections use WAL for read/write concurrency
- **Busy timeout:** 5000 ms to avoid immediate "database locked" errors

The `SessionManager` maintains a primary connection (`_get_db()`) for backward
compatibility and uses the pool via `_connect()` for concurrent operations.

---

## Maintenance

### Backup

- **On shutdown:** Automatic backup to `data/backups/sessions_YYYYMMDD_HHMMSS.db`
- **WAL checkpoint** before copy ensures consistency
- **Retention:** Last 10 backups kept; older ones auto-deleted
- **API:** `SessionManager.create_backup(backup_dir=None)`

### VACUUM

- **Scheduled:** Every `DB_VACUUM_INTERVAL_HOURS` hours (default 168 = weekly)
- **Runs:** `VACUUM` + `PRAGMA optimize`
- **Tracking:** Last run recorded in `_db_metadata` table
- **API:** `SessionManager.vacuum()`, `SessionManager.get_last_vacuum()`

---

## Design Decisions

1. **SQLite over PostgreSQL:** Single-file deployment, zero-config, sufficient
   for the expected load (single user, <10 concurrent agents).
2. **WAL mode:** Enables concurrent readers without blocking the writer.
3. **Lightweight migrations:** No Alembic dependency; version table + idempotent
   SQL statements are sufficient for this schema's complexity.
4. **Connection pool with primary fallback:** Methods using `_get_db()` use a
   dedicated connection for simplicity; pool-aware code uses `_connect()`.
5. **Unix epoch timestamps (REAL):** Consistent with Python's `time.time()`;
   easy arithmetic for age calculations and expiry checks.
6. **Denormalized message_count:** Trigger-maintained counter avoids expensive
   `COUNT(*)` JOINs on the project listing page.
