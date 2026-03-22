# LangGraph Research Notes for Refactor

## Key LangGraph Features to Leverage

### 1. Checkpointing (Persistence)
- **InMemorySaver** for dev, **SqliteSaver/PostgresSaver** for prod
- Automatic checkpoint at every super-step boundary
- Fault-tolerance: if a node fails, resume from last successful checkpoint
- **Pending writes**: successful parallel nodes don't re-run on retry
- Thread-based: each execution gets a `thread_id`

### 2. Subgraphs
- A graph used as a node in another graph
- Shared state keys: parent and child can share state channels automatically
- Private state: subgraph can have its own state keys not visible to parent
- Checkpointer propagates automatically to child subgraphs
- **Perfect for**: each agent task as a subgraph, Reflexion as a subgraph

### 3. Parallel Execution (Fan-out/Fan-in)
- Nodes with same predecessor run in parallel (same super-step)
- Use `Annotated[list, add]` reducer to merge parallel outputs
- Fan-out: multiple edges from one node
- Fan-in: multiple edges to one node
- LangGraph handles synchronization automatically

### 4. Command + Dynamic Routing
- `Command(goto='target_node', update={...})` for dynamic routing
- Can be returned from nodes to override static edges
- Useful for conditional routing (e.g., reflexion pass/fail)

### 5. Retry Policies
- Built-in retry with exponential backoff
- Applied per-node
- Handles transient errors (rate limits, timeouts)

### 6. Multi-Agent Patterns (from LangChain blog benchmarks)
- **Supervisor pattern**: central coordinator delegates to sub-agents
- Key improvements that yielded 50% performance increase:
  - Remove handoff messages from sub-agent context (reduces clutter)
  - Forward messages directly (don't paraphrase)
  - Better tool naming for handoff

## Mapping HiveMind Features to LangGraph

| HiveMind Feature | LangGraph Equivalent |
|---|---|
| DAG task ordering | StateGraph with edges (static + conditional) |
| Parallel task execution | Fan-out nodes (same super-step) |
| Task dependencies | Sequential edges |
| Reflexion (self-critique) | Subgraph with conditional loop |
| Blackboard/Notes | Shared state channel (Annotated[list, add]) |
| Git auto-commit | Post-node callback / node wrapper |
| Artifact tracking | Shared state channel |
| Confidence scoring | State field updated by each node |
| Remediation tasks | Command(goto='remediation_node') |
| Budget tracking | State field with reducer |
| Watchdog/timeout | Node-level timeout + retry policy |
| File conflict detection | Pre-node check in state |

## Architecture Plan

### Parent Graph: ProjectExecutor
- State: task_graph, completed_tasks, blackboard_notes, artifacts, budget, git_log
- Nodes: task_router → task_executor (subgraph) → post_task_handler → task_router (loop)
- task_router: picks next ready task based on dependency resolution
- post_task_handler: git commit, artifact registration, budget update

### Subgraph: TaskExecutor (per task)
- State: task_input, agent_output, reflexion_verdict, turn_count
- Nodes: run_agent → reflexion_check → (pass → END, fail → run_agent loop)
- Max 3 reflexion retries before marking as failed

### Subgraph: ReflexionLoop
- State: code_output, critique, verdict
- Nodes: critique_agent → evaluate → (pass → END, revise → critique_agent)
