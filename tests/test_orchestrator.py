"""
Test: verify orchestrator + sub-agent communication loop works end-to-end.

We mock the SDK to return controlled responses and verify the full cycle:
  1. User message → orchestrator
  2. Orchestrator emits <delegate> block → developer sub-agent runs
  3. Developer result fed back to orchestrator
  4. Orchestrator says TASK_COMPLETE
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdk_client import SDKResponse
from session_manager import SessionManager
from orchestrator import OrchestratorManager


updates = []
query_calls = []


async def mock_on_update(text: str):
    updates.append(text)


async def mock_query_with_retry(prompt, system_prompt, cwd, session_id=None,
                                 max_turns=10, max_budget_usd=2.0,
                                 max_retries=2, on_stream=None):
    """Mock SDK that simulates orchestrator→developer→orchestrator flow."""
    call_num = len(query_calls)
    query_calls.append({"prompt": prompt[:200], "system_prompt": system_prompt[:50]})

    if call_num == 0:
        # Orchestrator receives user message, delegates to developer
        return SDKResponse(
            text=(
                "I'll analyze this task and delegate the implementation.\n\n"
                '<delegate>\n'
                '{"agent": "developer", "task": "Create a hello.py file", "context": "Python"}\n'
                '</delegate>'
            ),
            session_id="orch-session-1",
            cost_usd=0.01,
            num_turns=1,
        )
    elif call_num == 1:
        # Developer sub-agent does the work
        return SDKResponse(
            text="I created hello.py with a greeting function. File saved.",
            session_id="dev-session-1",
            cost_usd=0.02,
            num_turns=2,
        )
    elif call_num == 2:
        # Orchestrator reviews developer result, completes
        return SDKResponse(
            text="The developer has completed the task. Everything looks good. TASK_COMPLETE",
            session_id="orch-session-2",
            cost_usd=0.01,
            num_turns=1,
        )
    else:
        return SDKResponse(text="Unexpected call", is_error=True, error_message="Too many calls")


async def main():
    global updates, query_calls
    updates = []
    query_calls = []

    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    session_mgr = SessionManager(db_path=db_path)
    await session_mgr.initialize()

    mock_sdk = MagicMock()
    mock_sdk.query_with_retry = mock_query_with_retry

    mgr = OrchestratorManager(
        project_name="test-project",
        project_dir="/tmp/test-project",
        sdk=mock_sdk,
        session_mgr=session_mgr,
        user_id=123,
        project_id="test-proj",
        on_update=mock_on_update,
        multi_agent=True,
    )

    await mgr.start_session("Create a hello world script")

    for _ in range(50):
        if not mgr.is_running:
            break
        await asyncio.sleep(0.1)

    # --- Assertions ---

    # Exactly 3 SDK calls: orchestrator, developer, orchestrator
    assert len(query_calls) == 3, f"Expected 3 SDK calls, got {len(query_calls)}"

    # Call 0: orchestrator got user message
    assert "Create a hello world" in query_calls[0]["prompt"]
    assert "Orchestrator" in query_calls[0]["system_prompt"]

    # Call 1: developer got delegated task with correct system prompt
    assert "Create a hello.py" in query_calls[1]["prompt"]
    assert "Developer" in query_calls[1]["system_prompt"]

    # Call 2: orchestrator got developer results back
    assert "Sub-agent results" in query_calls[2]["prompt"]
    assert "Orchestrator" in query_calls[2]["system_prompt"]

    # Conversation log has all participants
    agents_in_log = {m.agent_name for m in mgr.conversation_log}
    assert "user" in agents_in_log, "User message missing from log"
    assert "orchestrator" in agents_in_log, "Orchestrator missing from log"
    assert "developer" in agents_in_log, "Developer missing from log"

    # TASK_COMPLETE stopped the loop
    assert not mgr.is_running, "Manager should have stopped after TASK_COMPLETE"

    # Completion notification sent to user
    assert any("completed" in u.lower() for u in updates), "No completion notification sent"

    # Sessions persisted to SQLite
    orch_session = await session_mgr.get_session(123, "test-proj", "orchestrator")
    dev_session = await session_mgr.get_session(123, "test-proj", "developer")
    assert orch_session is not None, "Orchestrator session not saved"
    assert dev_session is not None, "Developer session not saved"

    # Messages persisted to DB
    db_messages = await session_mgr.get_recent_messages("test-proj", count=20)
    assert len(db_messages) >= 4, f"Expected >=4 messages in DB, got {len(db_messages)}"

    await session_mgr.close()

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
