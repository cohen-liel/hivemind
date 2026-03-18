# Test Plan — Hivemind Improvements PR

## Overview

This PR introduces **5 backend improvements** and **4 UI fixes** to the Hivemind multi-agent orchestration platform. All backend modules include comprehensive unit tests (96 tests total, all passing). This document describes the testing strategy, coverage, and manual verification steps.

## Commit Summary

| # | Commit | Files | Tests | Description |
|---|--------|-------|-------|-------------|
| 1 | `feat: add File Output Manager` | `file_output_manager.py` | 19 | JIT Context — agents reference files instead of passing full content |
| 2 | `feat: add Dynamic Spawner` | `dynamic_spawner.py` | 21 | Model-level fallback — retry failed tasks with a different Claude model |
| 3 | `feat: add Debate Engine` | `debate_engine.py` | 19 | Multi-perspective review for critical tasks (architecture, security, DB) |
| 4 | `feat: add Judge Agent` | `judge_agent.py` | 16 | Post-execution quality evaluation with structured scoring rubric |
| 5 | `feat: add Structured Notes` | `structured_notes.py` | 21 | Shared knowledge base — agents read/write notes visible to all |
| 6 | `feat: integrate all 5 improvements` | `dag_executor.py`, `orchestrator.py`, `isolated_query.py` | — | Wires all modules into the existing execution pipeline |
| 7 | `fix(ui): session popup, chat scroll, plan collapse` | 4 frontend files | — | UX improvements for the web interface |

## Automated Test Coverage

### Test Suites

All tests run with `pytest` and use a mock for `claude_agent_sdk` (defined in `tests/conftest.py`) since the SDK is a private package only available in the Claude Code runtime.

```bash
# Run all new tests
python3 -m pytest tests/test_file_output_manager.py tests/test_dynamic_spawner.py \
  tests/test_debate_engine.py tests/test_judge_agent.py tests/test_structured_notes.py -v
```

### 1. File Output Manager (19 tests)

| Test | What it verifies |
|------|-----------------|
| `test_register_and_retrieve` | Register an artifact and retrieve it by task_id |
| `test_register_multiple_artifacts` | Multiple artifacts per task are stored correctly |
| `test_get_artifacts_for_task_empty` | Returns empty list for unknown task_id |
| `test_get_all_artifacts` | Returns all artifacts across all tasks |
| `test_build_context_snippet_no_artifacts` | Returns empty string when no artifacts exist |
| `test_build_context_snippet_with_artifacts` | Generates correct file reference prompt for downstream agents |
| `test_build_context_snippet_for_specific_tasks` | Filters context to specific upstream task IDs |
| `test_extract_artifacts_from_output_no_artifacts` | Handles TaskOutput with no structured_artifacts |
| `test_extract_artifacts_from_output_with_artifacts` | Extracts file paths from TaskOutput.structured_artifacts |
| `test_extract_artifacts_code_blocks` | Extracts file paths from code blocks in agent output |
| `test_extract_artifacts_deduplication` | Deduplicates paths found in both artifacts and code blocks |
| `test_auto_register_from_output` | End-to-end: extract + register from a TaskOutput |
| `test_enhance_prompt_no_deps` | No modification when task has no dependencies |
| `test_enhance_prompt_with_deps` | Injects file references into prompt for dependent tasks |
| `test_enhance_prompt_with_dep_artifacts` | Only includes artifacts from actual dependency tasks |
| `test_thread_safety_register` | Concurrent registration from multiple threads |
| `test_clear` | Clears all artifacts |
| `test_summary` | Returns correct summary dict |
| `test_real_project_path_extraction` | Extracts real-world file paths from agent output |

### 2. Dynamic Spawner (21 tests)

| Test | What it verifies |
|------|-----------------|
| `test_model_cascade_order` | Model fallback order is correct (sonnet → haiku → opus) |
| `test_model_cascade_excludes_current` | Current model is excluded from fallback list |
| `test_model_cascade_unknown_model` | Returns full cascade for unknown models |
| `test_analyze_failure_timeout` | Classifies timeout errors correctly |
| `test_analyze_failure_rate_limit` | Classifies rate limit errors correctly |
| `test_analyze_failure_context_window` | Classifies context window errors correctly |
| `test_analyze_failure_generic` | Classifies generic errors as UNKNOWN |
| `test_should_try_model_switch_true` | Recommends model switch for eligible failures |
| `test_should_try_model_switch_false_no_models` | Does not recommend switch when cascade is empty |
| `test_should_try_model_switch_false_max_retries` | Respects max retry limit |
| `test_get_next_model_returns_first` | Returns first model from cascade |
| `test_get_next_model_skips_tried` | Skips already-tried models |
| `test_get_next_model_none_left` | Returns None when all models tried |
| `test_create_retry_context` | Creates correct retry context dict |
| `test_create_retry_context_preserves_history` | Appends to existing tried_models list |
| `test_format_retry_guidance_timeout` | Generates correct guidance for timeout failures |
| `test_format_retry_guidance_context` | Generates correct guidance for context window failures |
| `test_format_retry_guidance_rate_limit` | Generates correct guidance for rate limit failures |
| `test_full_flow_success` | End-to-end: failure → analysis → model switch recommendation |
| `test_full_flow_exhausted` | End-to-end: all models tried → no more switches |
| `test_spawner_is_stateless` | Verifies spawner has no mutable state between calls |

### 3. Debate Engine (19 tests)

| Test | What it verifies |
|------|-----------------|
| `test_should_debate_architecture_role` | Triggers debate for ARCHITECT role |
| `test_should_debate_database_role` | Triggers debate for DATABASE_EXPERT role |
| `test_should_debate_security_role` | Triggers debate for SECURITY_AUDITOR role |
| `test_should_debate_frontend_role` | Does NOT trigger for FRONTEND_DEV role |
| `test_should_debate_keyword_security` | Triggers for "security" keyword in goal |
| `test_should_debate_keyword_database` | Triggers for "database migration" keyword |
| `test_should_debate_keyword_auth` | Triggers for "authentication" keyword |
| `test_should_debate_no_match` | Does NOT trigger for generic goals |
| `test_should_debate_disabled` | Returns False when debate is disabled |
| `test_get_challenger_role_for_architect` | Returns correct challenger for ARCHITECT |
| `test_get_challenger_role_for_database` | Returns correct challenger for DATABASE_EXPERT |
| `test_get_challenger_role_for_security` | Returns correct challenger for SECURITY_AUDITOR |
| `test_get_challenger_role_fallback` | Returns REVIEWER as fallback challenger |
| `test_build_debate_prompt` | Builds correct prompt with proposal and context |
| `test_build_judge_prompt` | Builds correct judge prompt with both perspectives |
| `test_parse_verdict_approve` | Parses APPROVE verdict correctly |
| `test_parse_verdict_revise` | Parses REVISE verdict correctly |
| `test_parse_verdict_fallback` | Falls back to APPROVE on unparseable response |
| `test_run_debate_full_flow` | End-to-end: proposal → challenge → verdict via SDK |

### 4. Judge Agent (16 tests)

| Test | What it verifies |
|------|-----------------|
| `test_build_evaluation_prompt` | Builds correct evaluation prompt with all dimensions |
| `test_build_evaluation_prompt_with_context` | Includes original request context in prompt |
| `test_parse_scores_valid` | Parses well-formatted JSON scores correctly |
| `test_parse_scores_from_code_block` | Extracts JSON from markdown code blocks |
| `test_parse_scores_missing_dimension` | Handles missing dimensions gracefully |
| `test_parse_scores_invalid_json` | Returns default scores on invalid JSON |
| `test_parse_scores_out_of_range` | Clamps scores to 1-10 range |
| `test_compute_verdict_pass` | Computes PASS verdict when all scores above threshold |
| `test_compute_verdict_fail` | Computes FAIL verdict when scores below threshold |
| `test_compute_verdict_warn` | Computes WARN verdict for borderline scores |
| `test_compute_verdict_custom_threshold` | Respects custom threshold parameter |
| `test_format_verdict_summary` | Formats human-readable verdict summary |
| `test_evaluate_dag_results_pass` | End-to-end: evaluates DAG results → PASS |
| `test_evaluate_dag_results_fail` | End-to-end: evaluates DAG results → FAIL |
| `test_evaluate_dag_results_empty` | Handles empty DAG results gracefully |
| `test_dimensions_are_complete` | Verifies all 6 evaluation dimensions are defined |

### 5. Structured Notes (21 tests)

| Test | What it verifies |
|------|-----------------|
| `test_add_note` | Adds a note with correct fields |
| `test_add_note_auto_timestamp` | Auto-generates timestamp |
| `test_get_notes_empty` | Returns empty list for new project |
| `test_get_notes_by_category` | Filters notes by category |
| `test_get_notes_by_agent` | Filters notes by agent role |
| `test_get_notes_combined_filter` | Filters by both category and agent |
| `test_get_recent_notes` | Returns only N most recent notes |
| `test_format_notes_for_prompt` | Formats notes as readable prompt text |
| `test_format_notes_empty` | Returns empty string for no notes |
| `test_format_notes_max_length` | Truncates to max character length |
| `test_extract_notes_from_output` | Extracts NOTES blocks from agent output |
| `test_extract_notes_multiple` | Extracts multiple NOTES blocks |
| `test_extract_notes_none` | Returns empty list when no NOTES blocks |
| `test_auto_record_from_output` | End-to-end: extract + record from agent output |
| `test_clear_notes` | Clears all notes for a project |
| `test_get_summary` | Returns correct summary statistics |
| `test_persistence_across_instances` | Notes persist across NotesManager instances (same store) |
| `test_categories_enum` | All expected categories are defined |
| `test_note_ordering` | Notes are returned in chronological order |
| `test_concurrent_writes` | Thread-safe concurrent note addition |
| `test_build_agent_instruction` | Builds correct instruction text for agents |

## Integration Points

The integration commit (`feat: integrate all 5 improvements`) modifies three existing files:

### `dag_executor.py` Changes

| Change | Location | What it does |
|--------|----------|-------------|
| Import new modules | Top of file | Lazy imports for `file_output_manager`, `dynamic_spawner`, `structured_notes` |
| `_ExecutionContext` fields | Class definition | Added `artifact_registry`, `spawner`, `notes_manager` |
| Prompt injection | `_run_single_task` (after skill injection) | Injects file artifact references + structured notes into agent prompt |
| Artifact registration | `_run_single_task` (after task completion) | Registers output artifacts + extracts structured notes |
| Dynamic spawner | `_handle_failure` (before remediation) | Tries model switch before creating remediation task |
| Model override | `isolated_query` call | Passes `model_override` from task metadata |

### `orchestrator.py` Changes

| Change | Location | What it does |
|--------|----------|-------------|
| Debate Engine | Between PM graph creation and DAG execution | Runs debate for critical tasks (architecture, security, DB) |
| Judge Agent | Between DAG execution and memory update | Evaluates DAG results quality with structured scoring |

### `isolated_query.py` Changes

| Change | What it does |
|--------|-------------|
| Added `model: str | None = None` parameter | Allows callers to override the Claude model for a specific query |
| Pass `model` to `ClaudeAgentOptions` | Enables Dynamic Spawner to retry with a different model |

## Manual Testing Checklist

### Backend (requires Claude Code SDK)

- [ ] Run a project with architecture task → verify Debate Engine triggers
- [ ] Run a project with simple frontend task → verify Debate Engine does NOT trigger
- [ ] Force a task failure → verify Dynamic Spawner tries a different model
- [ ] Check that file artifacts appear in downstream agent prompts
- [ ] Check that structured notes are written and read between agents
- [ ] Check that Judge Agent produces a quality verdict after DAG completion
- [ ] Verify that `model` parameter in `isolated_query` correctly switches models

### Frontend

- [ ] Open a project with completed session → dismiss the summary popup → reload page → verify popup stays dismissed
- [ ] Start a new session on same project → verify popup reappears with new summary
- [ ] Open a project → verify chat scrolls to bottom on load
- [ ] Send a message → verify chat scrolls to bottom
- [ ] Scroll up in chat → verify "New messages" indicator appears → click it → verify instant scroll to bottom
- [ ] Open PlanView with 4+ tasks, some completed → verify "Hide completed tasks" toggle appears
- [ ] Click toggle → verify completed tasks are hidden → click again → verify they reappear

## Architecture Diagram

```
User Request
    │
    ▼
┌──────────────┐
│  Orchestrator │
│              │
│  1. Architect Agent (codebase survey)
│  2. Cross-Project Memory (lessons)
│  3. PM Agent → TaskGraph
│  4. ★ Debate Engine (critical tasks)  ← NEW
│  5. DAG Executor
│     ├─ ★ File Output Manager          ← NEW
│     ├─ ★ Structured Notes             ← NEW
│     ├─ ★ Dynamic Spawner (on failure) ← NEW
│     └─ Agent execution via SDK
│  6. ★ Judge Agent (quality eval)      ← NEW
│  7. Memory Agent (update lessons)
│              │
└──────────────┘
    │
    ▼
  Results
```

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Debate Engine adds latency to critical tasks | Only triggers for architecture/security/DB tasks; adds ~2 extra SDK calls |
| Judge Agent false negatives | Threshold is configurable; defaults to 6/10 which is lenient |
| Dynamic Spawner model cascade | Falls back gracefully; if all models fail, proceeds to normal remediation |
| File Output Manager disk usage | Only stores metadata (paths), not file contents |
| Structured Notes memory growth | Notes are per-session, cleared on new session |
| UI localStorage persistence | Scoped per-project; no cross-project interference |

## Dependencies

**No new external dependencies.** All modules use only:
- Python stdlib (`json`, `re`, `dataclasses`, `pathlib`, `threading`, `time`)
- Existing project modules (`contracts`, `config`, `isolated_query`)
- Claude Agent SDK (already a project dependency)
