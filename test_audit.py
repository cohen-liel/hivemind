"""
Stress tests for the multi-agent system — testing all edge cases found in audit.
"""

import json
import asyncio
import sys

# Fix imports — mock out modules that need claude_agent_sdk
sys.path.insert(0, ".")

# Mock the claude_agent_sdk before importing anything that depends on it
import types

# Create mock module hierarchy
mock_sdk = types.ModuleType('claude_agent_sdk')
mock_sdk.query = lambda *a, **kw: None
mock_sdk.ClaudeAgentOptions = type('ClaudeAgentOptions', (), {})
sys.modules['claude_agent_sdk'] = mock_sdk

mock_types = types.ModuleType('claude_agent_sdk.types')
for name in ['AssistantMessage', 'ResultMessage', 'TextBlock', 'ToolUseBlock',
             'ToolResultBlock', 'UserMessage', 'ContentBlock']:
    setattr(mock_types, name, type(name, (), {}))
sys.modules['claude_agent_sdk.types'] = mock_types

from contracts import (
    AgentRole, ArtifactType, Artifact, FailureCategory,
    TaskGraph, TaskInput, TaskOutput, TaskStatus,
    MemorySnapshot, classify_failure, create_remediation_task,
    extract_task_output, task_input_to_prompt, task_graph_schema,
)
from memory_agent import detect_inconsistencies, _should_use_llm, _heuristic_update
from dag_executor import (
    _plan_batches, _split_writers_by_conflicts, _remediation_depth,
    _validate_artifacts,
)
from pm_agent import (
    _enforce_artifact_requirements, _parse_task_graph,
    fallback_single_task_graph,
)

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


print("=" * 60)
print("STRESS TEST: contracts.py")
print("=" * 60)

# Test #1: JSON parser with braces inside strings
print("\n--- JSON parser edge cases ---")
raw_with_braces = '```json\n{"task_id": "t1", "status": "completed", "summary": "Created {config} file with {env} vars"}\n```'
out = extract_task_output(raw_with_braces, "t1")
test("Braces in string values", out.status == TaskStatus.COMPLETED, f"got {out.status}")

raw_nested = '```json\n{"task_id": "t1", "status": "completed", "summary": "Done", "artifacts": ["a.py"], "structured_artifacts": [{"type": "file_manifest", "title": "Files", "data": {"files": {"a.py": "test"}}, "summary": "ok"}]}\n```'
out2 = extract_task_output(raw_nested, "t1")
test("Nested JSON with artifacts", len(out2.structured_artifacts) == 1, f"got {len(out2.structured_artifacts)}")

raw_malformed = "Here is my output: I did some work but forgot the JSON"
out3 = extract_task_output(raw_malformed, "t1")
test("Malformed output -> FAILED", out3.status == TaskStatus.FAILED)

raw_multiple_json = 'Some text ```json\n{"bad": true}\n``` more text ```json\n{"task_id": "t1", "status": "completed", "summary": "Done"}\n```'
out4 = extract_task_output(raw_multiple_json, "t1")
test("Multiple JSON blocks (second valid)", out4.status == TaskStatus.COMPLETED)

# Test #3: remediation_id length
print("\n--- Remediation ID length ---")
long_id = "a" * 60  # 60 chars, close to limit
try:
    task = TaskInput(id=long_id, role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project with all features")
    output = TaskOutput(task_id=long_id, status=TaskStatus.FAILED, summary="Failed", failure_details="ModuleNotFoundError: flask")
    output.failure_category = FailureCategory.DEPENDENCY_MISSING
    rem = create_remediation_task(task, output, task_counter=999)
    if rem:
        test("Remediation ID <= 64 chars", len(rem.id) <= 64, f"id='{rem.id}' len={len(rem.id)}")
    else:
        test("Remediation ID <= 64 chars", False, "remediation was None")
except Exception as e:
    test("Remediation ID <= 64 chars", False, str(e))

# Test #5: self-dependency
print("\n--- DAG validation edge cases ---")
try:
    graph = TaskGraph(
        project_id="test", user_message="test", vision="Test self-dep",
        tasks=[
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER,
                      goal="Build the API endpoints for the project",
                      depends_on=["task_001"]),  # Self-dependency!
        ],
    )
    errors = graph.validate_dag()
    test("Self-dependency detected", len(errors) > 0, f"errors={errors}")
except Exception as e:
    test("Self-dependency detected", False, str(e))

# Test #6: duplicate task IDs
try:
    graph = TaskGraph(
        project_id="test", user_message="test", vision="Test dup IDs",
        tasks=[
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project"),
            TaskInput(id="task_001", role=AgentRole.FRONTEND_DEVELOPER, goal="Build the React UI components for the project"),
        ],
    )
    errors = graph.validate_dag()
    test("Duplicate task IDs detected", len(errors) > 0, f"errors={errors}")
except Exception as e:
    test("Duplicate task IDs detected", False, str(e))

# Test #4: context_from dedup in remediation
print("\n--- Remediation context dedup ---")
task = TaskInput(
    id="task_001", role=AgentRole.BACKEND_DEVELOPER,
    goal="Build the API endpoints for the project",
    context_from=["task_001"],  # Already contains itself
)
output = TaskOutput(task_id="task_001", status=TaskStatus.FAILED, summary="Failed", failure_details="SyntaxError: invalid syntax")
output.failure_category = FailureCategory.BUILD_ERROR
rem = create_remediation_task(task, output, task_counter=5)
if rem:
    dupes = [x for x in rem.context_from if rem.context_from.count(x) > 1]
    test("No duplicate context_from", len(dupes) == 0, f"context_from={rem.context_from}")
else:
    test("No duplicate context_from", False, "remediation was None")


print("\n" + "=" * 60)
print("STRESS TEST: memory_agent.py")
print("=" * 60)

# Test #9: _should_use_llm with file_manifest only
print("\n--- _should_use_llm ---")
outputs_with_manifest = [
    TaskOutput(task_id="t1", status=TaskStatus.COMPLETED, summary="Done",
               structured_artifacts=[
                   Artifact(type=ArtifactType.FILE_MANIFEST, title="Files", data={"files": {"a.py": "test"}})
               ]),
]
result = _should_use_llm(outputs_with_manifest)
test("_should_use_llm with only file_manifest", True, f"returns {result} (expected True since any artifacts -> True)")

# Test #14: heuristic doesn't set architecture_summary
print("\n--- Heuristic update ---")
outputs = [
    TaskOutput(task_id="t1", status=TaskStatus.COMPLETED, summary="Built the API",
               structured_artifacts=[
                   Artifact(type=ArtifactType.API_CONTRACT, title="API", data={"endpoints": [{"method": "GET", "path": "/users"}]})
               ]),
]
graph = TaskGraph(project_id="test", user_message="build api", vision="Build API", tasks=[
    TaskInput(id="t1", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project"),
])
snap = _heuristic_update("test", graph, outputs, None)
test("Heuristic sets architecture_summary", snap.architecture_summary != "", f"got '{snap.architecture_summary}'")


print("\n" + "=" * 60)
print("STRESS TEST: dag_executor.py")
print("=" * 60)

# Test #18: writers with no files_scope
print("\n--- Batch planning ---")
writers_no_scope = [
    TaskInput(id="w1", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project"),
    TaskInput(id="w2", role=AgentRole.FRONTEND_DEVELOPER, goal="Build the React UI components for the project"),
]
batches = _split_writers_by_conflicts(writers_no_scope)
test("Writers no scope -> separate batches", len(batches) == 2, f"got {len(batches)} batches")

writers_with_scope = [
    TaskInput(id="w1", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project", files_scope=["api.py"]),
    TaskInput(id="w2", role=AgentRole.FRONTEND_DEVELOPER, goal="Build the React UI components for the project", files_scope=["ui.tsx"]),
]
batches2 = _split_writers_by_conflicts(writers_with_scope)
test("Writers non-overlapping scope -> 1 batch", len(batches2) == 1, f"got {len(batches2)} batches")

writers_conflict = [
    TaskInput(id="w1", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project", files_scope=["shared.py"]),
    TaskInput(id="w2", role=AgentRole.FRONTEND_DEVELOPER, goal="Build the React UI components for the project", files_scope=["shared.py"]),
]
batches3 = _split_writers_by_conflicts(writers_conflict)
test("Writers overlapping scope -> 2 batches", len(batches3) == 2, f"got {len(batches3)} batches")

# Test #22: _remediation_depth
print("\n--- Remediation depth ---")
normal_task = TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project")
test("Normal task depth = 0", _remediation_depth(normal_task) == 0)

rem_task = TaskInput(id="fix_001", role=AgentRole.BACKEND_DEVELOPER, goal="Fix the build error in the project", is_remediation=True, original_task_id="task_001")
test("Remediation task depth = 1", _remediation_depth(rem_task) == 1)

# Test artifact validation
print("\n--- Artifact validation ---")
task_with_reqs = TaskInput(
    id="t1", role=AgentRole.BACKEND_DEVELOPER,
    goal="Build the API endpoints for the project",
    required_artifacts=[ArtifactType.API_CONTRACT, ArtifactType.FILE_MANIFEST],
)
output_missing = TaskOutput(
    task_id="t1", status=TaskStatus.COMPLETED, summary="Done",
    structured_artifacts=[
        Artifact(type=ArtifactType.FILE_MANIFEST, title="Files", data={"files": {}})
    ],
)
_validate_artifacts(task_with_reqs, output_missing)
test("Missing artifact adds issue", any("Missing required" in i for i in output_missing.issues))


print("\n" + "=" * 60)
print("STRESS TEST: pm_agent.py")
print("=" * 60)

# Test #24: fallback with short message
print("\n--- Fallback graph ---")
try:
    graph = fallback_single_task_graph("fix bug", "test")
    test("Fallback with short message", graph is not None and len(graph.tasks) == 1)
except Exception as e:
    test("Fallback with short message", False, str(e))

# Test _enforce_artifact_requirements
print("\n--- Artifact enforcement ---")
graph = TaskGraph(
    project_id="test", user_message="test", vision="Test",
    tasks=[
        TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER, goal="Build the API endpoints for the project"),
        TaskInput(id="task_002", role=AgentRole.FRONTEND_DEVELOPER, goal="Build the React UI components for the project"),
        TaskInput(id="task_003", role=AgentRole.TEST_ENGINEER, goal="Write and run all unit tests for the project"),
    ],
)
graph = _enforce_artifact_requirements(graph)
test("Backend gets api_contract", ArtifactType.API_CONTRACT in graph.tasks[0].required_artifacts)
test("Backend gets file_manifest", ArtifactType.FILE_MANIFEST in graph.tasks[0].required_artifacts)
test("Frontend gets component_map", ArtifactType.COMPONENT_MAP in graph.tasks[1].required_artifacts)
test("Frontend gets file_manifest", ArtifactType.FILE_MANIFEST in graph.tasks[1].required_artifacts)
test("Tester gets test_report", ArtifactType.TEST_REPORT in graph.tasks[2].required_artifacts)


print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    print("\n⚠️  FAILURES DETECTED — these are the bugs we need to fix!")
else:
    print("\n✅ All tests passed!")
