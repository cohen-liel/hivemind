# OSS Tools to Test from the Document

## Tools that can be benchmarked (affect code generation quality):

1. **ChromaDB** - Vector DB for cross-project memory (replace flat JSON)
   - Package: `chromadb`
   - Replaces: `cross_project_memory.py` flat JSON search
   - Test: Inject ChromaDB-retrieved lessons vs JSON-retrieved lessons into agent prompts

2. **LangGraph** - State machine for DAG execution
   - Package: `langgraph`
   - Replaces: `dag_executor.py` custom DAG logic
   - Test: Build equivalent DAG with LangGraph, run same 3 projects

3. **LangChain Core** - Model abstraction layer
   - Package: `langchain-core`, `langchain-openai`
   - Replaces: `isolated_query_openai.py` / `agent_runtime.py`
   - Test: Use LangChain agent with same tools, run same 3 projects

4. **filelock** - OS-level file locking
   - Package: `filelock`
   - Replaces: raw JSON read/write in cross_project_memory.py
   - Test: Concurrent write test (already proven this bug exists)

## Tools that are infrastructure-only (can't benchmark code quality):

5. **Docker** - Sandbox for BashRuntime (security, not quality)
6. **Alembic** - DB migrations (dev workflow, not quality)
7. **xState** - Alternative to LangGraph (JS-only, not applicable)

## Plan: Test tools 1-4 via A/B benchmark
