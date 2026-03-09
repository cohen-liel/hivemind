# UI/UX Diagnosis — What's Broken and Why

## User Complaints
1. Can't see what agents are doing — just "agent running"
2. Failures without explanation — agent fails, no idea why
3. Chat is silent — Orchestrator works but nothing shows
4. Plan view shows nothing useful

## Root Cause Analysis

### Problem 1: Chat is silent during Orchestrator work
**Backend sends**: `agent_update` with `summary` field (text from agent stream)
**Frontend reducer**: WS_AGENT_UPDATE only adds to activity log if `summaryText.length > 25` AND it's different from last summary
**Issue**: The `agent_update` events from orchestrator heartbeat only have `text` field (not `summary`), so they update the ticker but DON'T appear in the chat. The orchestrator heartbeat sends: `text=f"🎯 {phase} ({elapsed}s)"` but NO `summary` field.

**Fix**: In orchestrator.py `_orch_heartbeat()`, add `summary=` field to agent_update events so they appear in chat.

### Problem 2: Sub-agent activity invisible in chat  
**Backend sends**: `tool_use` events from `_on_dag_agent_tool_use` — these DO appear in chat
**Backend sends**: `agent_update` from `_on_dag_agent_text` — has `summary` field, should appear
**Issue**: The `agent_update` from `_on_dag_agent_text` only fires for text > 20 chars. Many agent activities are short. Also, the `tool_use` events from DAG agents use `tool` instead of `tool_name` field!

**Fix**: In orchestrator.py `_on_dag_agent_tool_use`, the event sends `tool=tool_name` but frontend expects `tool_name=`. Need to add `tool_name=tool_name`.

### Problem 3: Failures without clear explanation
**Backend sends**: `agent_finished` with `failure_reason` field — this IS sent correctly
**Frontend**: `AgentFinishedBubble` only shows "Failed" with cost/turns/duration stats but DOESN'T show `failure_reason`!

**Fix**: Show `failure_reason` in AgentFinishedBubble.

### Problem 4: Plan view empty
**Primary source**: DAG graph from `task_graph` event — works IF PM creates a DAG
**Fallback**: Parses orchestrator text for numbered steps — very fragile regex
**Issue**: If orchestrator doesn't output numbered steps AND no DAG is created (legacy mode), plan is empty. Also, the fallback only matches `agent_text` from `orchestrator` agent, but orchestrator summaries come through `agent_update` not `agent_text`.

**Fix**: 
1. Make orchestrator emit its plan as `agent_text` type (not just agent_update)
2. Improve fallback plan extraction to also look at delegation events (already does this)
3. Show more info in plan view even without explicit plan

### Problem 5: No visibility into what Orchestrator is deciding
**Current**: Orchestrator heartbeat shows generic phases like "analyzing request...", "reviewing context..."
**Better**: After orchestrator responds, emit the actual plan/decision as a chat message

**Fix**: After orchestrator response is parsed, emit the delegation plan as an agent_text event.

## Summary of Fixes Needed

### Backend (orchestrator.py):
1. `_orch_heartbeat`: Add `summary=` field to agent_update events
2. `_on_dag_agent_tool_use`: Add `tool_name=tool_name` to event (frontend expects this)
3. After parsing delegations: Emit orchestrator's plan as `agent_result` so it shows in chat
4. After parsing delegations: Emit a structured plan summary for the Plan view

### Frontend:
1. `AgentFinishedBubble`: Show `failure_reason` when present
2. `PlanView extractPlan`: Also look at `agent_update` events from orchestrator (not just agent_text)
3. `projectReducer WS_TOOL_USE`: Handle both `tool` and `tool_name` fields
