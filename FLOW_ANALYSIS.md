# Flow Analysis: Why Backend shows ACTIVE but not progressing

## The complete flow:
1. User sends task → orchestrator starts
2. Orchestrator calls Claude SDK → gets response with `<delegate>` blocks
3. `_parse_delegations()` extracts delegations
4. For each delegation: `agent_states[agent] = {state: "working"}` + emit `delegation` event
5. `_run_sub_agents()` starts → groups by role → `run_role()` for each
6. Inside `run_role()`:
   a. Emit `agent_started` event
   b. Build sub_prompt with context
   c. Start heartbeat task (every 5s)
   d. Call `_query_agent()` → which calls `sdk_manager.query()` → which calls `_consume_stream()`
   e. SDK spawns Claude CLI subprocess
   f. Stream messages come back with tool_use, text, etc.
   g. On finish: emit `agent_finished`

## Where the "ACTIVE but not progressing" happens:

### Scenario 1: SDK subprocess takes long to start
- Between `agent_started` and first SDK message, there's a delay
- The heartbeat task (step 6c) sends updates every 5s with phases:
  "reading codebase...", "analyzing code...", etc.
- BUT these are FAKE phases — they don't reflect actual SDK activity
- If SDK is slow to start, user sees "reading codebase... (5s)" but nothing is happening

### Scenario 2: Orchestrator finishes but agent is still "working"
- From screenshot: orchestrator shows "Finished" but Backend shows "ACTIVE 1m48s"
- This means the orchestrator's finally block ran (emitting project_status: idle)
- BUT the Backend agent's `agent_finished` was never emitted
- This could happen if:
  a. The orchestrator was interrupted/cancelled before sub-agents finished
  b. An exception occurred that bypassed the agent_finished emission

### Scenario 3: The "0 rounds, $0.0000" mystery
- The banner shows "0 rounds, $0.0000" — this means the orchestrator itself 
  didn't actually run any SDK calls (or they failed silently)
- But Backend is shown as ACTIVE — meaning delegation event was sent
- This suggests the orchestrator delegated work but then crashed/was interrupted
  before the sub-agents could start

## Key insight from the screenshots:
The user sees:
- "Interrupted Task Found" banner
- Orchestrator "Finished ($0.0000, 1 turns, 0s)"
- Backend "ACTIVE 29s" → "ACTIVE 1m48s"

This is a RACE CONDITION:
1. Orchestrator starts, sends delegation to Backend
2. Backend gets `agent_started` event → shows ACTIVE
3. Something interrupts the orchestrator (crash, error, user stop)
4. Orchestrator's finally block runs → emits `agent_finished` for orchestrator
5. BUT the Backend agent_finished was never sent because the sub-agent task was cancelled
6. Frontend shows Backend still ACTIVE because no agent_finished was received

## FIX ALREADY APPLIED:
The fix in the finally blocks (emitting agent_finished for all working agents)
should handle this. But there might be an additional issue:
- The `WS_AGENT_FINISHED` handler in the reducer might not properly close the agent card
- Need to verify the frontend handles the cleanup correctly
