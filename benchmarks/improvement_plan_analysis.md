# Improvement Plan Analysis

## Items from the document to test/validate:

### Phase 1: Critical Bug Fixes
1. **PM Agent JSON Parsing Trap** - pm_agent.py `_parse_task_graph` - brace counting fails on brainstorm tags
2. **Thread-Safety in cross_project_memory.py** - no file locking on JSON writes
3. **Global Exception Handler masking** - server.py & isolated_query.py swallowing anyio errors
4. **State Management Race Conditions** - state.py get_manager() dict iteration without lock

### Phase 2: Security
5. **Unsandboxed BashRuntime** - direct subprocess execution
6. **Orphan Process PID diffing** - fragile process cleanup

### Phase 3: Architecture
7. **Monolithic orchestrator.py** - needs splitting
8. **Manual DB migrations** - should use alembic
9. **Vector DB for Memory** - ChromaDB/Pinecone for cross_project_memory

### Phase 4: Open-Source Integration
10. **LangGraph for DAG** - replace custom dag_executor
11. **LangChain Core** - standardize model interface

## What we can actually test with real benchmarks:
- Bug #1 (PM JSON parsing) - can we trigger it?
- Bug #2 (file locking) - can we reproduce corruption?
- Bug #9 (Vector DB) - ChromaDB vs flat JSON performance
- Bug #10 (LangGraph) - PoC comparison
