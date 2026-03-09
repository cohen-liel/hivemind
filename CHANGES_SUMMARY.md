# Changes Summary — Agent Intelligence & UI Fixes

This document summarizes all changes made to the `web-claude-bot` project based on Anthropic's official guidelines for building effective AI agents.

---

## 1. Brain Upgrade: XML-Structured Prompts (`config.py`)

All system prompts were converted from plain-text with `═══` headers to structured XML tags. This is the single most impactful change because Claude is specifically trained to parse and respect XML tag boundaries.

### What Changed

| Prompt | Before | After |
|--------|--------|-------|
| `ORCHESTRATOR_SYSTEM_PROMPT` | Plain text with `═══` dividers | `<role>`, `<architecture>`, `<planning_protocol>`, `<delegation_format>`, `<constraints>` |
| `SOLO_AGENT_PROMPT` | Plain text | `<role>`, `<instructions>`, `<output_format>`, `<constraints>` |
| `SUB_AGENT_PROMPTS` (developer, reviewer, tester, devops, researcher) | Plain text with `---` dividers | `<role>`, `<responsibilities>`, `<output_format>`, `<constraints>` |
| `SPECIALIST_PROMPTS` (all 10+ specialists) | Plain text | `<role>`, `<expertise>`, `<instructions>`, `<output_format>` |
| `_AGENT_COLLABORATION_FOOTER` | Plain text | `<collaboration_protocol>` with nested `<artifacts>`, `<communication>`, `<quality>` |
| `_TYPED_CONTRACT_FOOTER` | Plain text | `<contract_protocol>` with `<thinking>` step added before JSON output |

### Why This Matters

According to Anthropic's "Effective Context Engineering" guide, Claude processes XML tags as **structural boundaries** — similar to how HTML tags work in a browser. Without XML tags, the model treats all text as a flat stream and may "forget" or deprioritize instructions that appear in the middle of a long prompt. With XML tags, each section has a clear semantic boundary that the model respects.

---

## 2. Thinking Before Acting (`config.py` — `_TYPED_CONTRACT_FOOTER`)

Added a mandatory `<agent_thinking>` step before the JSON output in the Typed Contract Footer. Previously, agents jumped directly from reading the task to producing structured JSON output. Now they must:

1. Analyze the task requirements inside `<agent_thinking>` tags
2. Plan their approach
3. Only then produce the JSON output

This follows Anthropic's recommendation for **extended thinking** — giving the model space to reason before committing to an answer significantly improves output quality, especially for complex multi-step tasks.

---

## 3. Skills Injection Optimization (`skills_registry.py` + `dag_executor.py`)

### Problem

The system was injecting up to 5 skills per agent (each up to 4,000 characters), adding up to 20,000 characters of context that diluted the agent's focus on its actual task. Anthropic calls this **Context Rot** — when the context window is filled with marginally relevant information, the model's attention on critical instructions degrades.

### Changes

| File | Before | After |
|------|--------|-------|
| `skills_registry.py` — `select_skills_for_task()` | `max_skills=5` default | `max_skills=2` default |
| `skills_registry.py` — `build_skill_prompt()` | Plain text format | XML `<available_skills>` wrapper with `<skill>` tags |
| `dag_executor.py` — `_run_single_task()` | `max_skills=5` hardcoded | Uses default (2) from `select_skills_for_task()` |

---

## 4. Bug Fix: DAG Events Not Reaching Frontend (`orchestrator.py`)

### The Bug

This was the root cause of the broken agent status display. In DAG mode (the Typed Contract Protocol path), the callbacks `_on_dag_task_start` and `_on_dag_task_done` were emitting `agent_update` events instead of `agent_started` and `agent_finished` events.

The frontend specifically listens for `agent_started` to:
- Set the agent's state to `working` with a `started_at` timestamp
- Add an entry to the Activity Feed
- Start tracking SDK call duration in the Network Trace

And `agent_finished` to:
- Set the agent's state to `done` or `error`
- Record `last_result` and `duration`
- Complete the SDK call entry in the Network Trace

Since the DAG path was emitting `agent_update` instead, **none of these features worked** in DAG mode. The non-DAG path (legacy `_run_sub_agent`) correctly emitted `agent_started`/`agent_finished`, which is why it "used to work" — it broke when the system switched to DAG execution.

### The Fix

| Callback | Before | After |
|----------|--------|-------|
| `_on_dag_task_start` | `_emit_event("agent_update", ...)` | `_emit_event("agent_started", agent=..., task=...)` |
| `_on_dag_task_done` | `_emit_event("agent_update", ...)` | `_emit_event("agent_finished", agent=..., cost=..., turns=..., is_error=...)` |

The full EventBus pipeline was verified to be intact: `orchestrator._emit_event()` → `on_event` callback in `dashboard/api.py` → `event_bus.publish()` → WebSocket fan-out + DB persistence. The `_PERSIST_EVENT_TYPES` set in `dashboard/events.py` already includes both `agent_started` and `agent_finished`.

---

## 5. UI Fix: All Communication Through Orchestrator (`Controls.tsx` + `ProjectView.tsx`)

### Problem

The frontend allowed users to select a specific agent from a dropdown and send messages directly to it, bypassing the Orchestrator. This breaks the Orchestrator-Workers architecture — the Orchestrator loses track of what's happening, can't coordinate, and the conversation history becomes fragmented.

### Changes

| File | Change |
|------|--------|
| `Controls.tsx` | Removed the `targetAgent` dropdown selector. The input field now always sends to the Orchestrator. Added a visual indicator showing "All messages go through the Orchestrator" |
| `ProjectView.tsx` — `handleSend()` | Removed the `agent?: string` parameter and the `talkToAgent()` code path. All messages now go through `sendMessage()` which routes to the Orchestrator |

---

## Files Modified

| File | Type of Change |
|------|---------------|
| `config.py` | All prompts converted to XML structure |
| `skills_registry.py` | Reduced max skills to 2, XML format for injection |
| `dag_executor.py` | Removed hardcoded `max_skills=5` |
| `orchestrator.py` | Fixed DAG event types (`agent_started`/`agent_finished`) |
| `frontend/src/components/Controls.tsx` | Removed agent dropdown |
| `frontend/src/pages/ProjectView.tsx` | Removed direct agent messaging path |
