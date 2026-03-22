# OSS Outsourcing — Open-Source Component Integration

This document describes the open-source libraries integrated into HiveMind to replace hand-rolled implementations with battle-tested, community-maintained alternatives.

## Philosophy

> **Keep what's unique, outsource what's commodity.**

HiveMind's core value is its DAG-based multi-agent orchestration engine. Everything around it — context compression, memory search, model routing, and process isolation — is better served by specialized open-source projects that have dedicated teams, extensive testing, and active communities.

All integrations follow the same pattern:
- **Transparent fallback**: If the library is not installed, the original behavior is preserved
- **Environment-variable configuration**: No code changes needed to enable/disable
- **Dual-write safety**: Critical data (memory) is written to both old and new backends

---

## Components

### 1. LLMLingua — Context Compression

| | |
|---|---|
| **Replaces** | Hand-rolled `compress_context_entry()` in `orch_context.py` |
| **Library** | [Microsoft LLMLingua-2](https://github.com/microsoft/LLMLingua) |
| **Why** | Achieves 2x-5x token compression with minimal semantic loss using trained models, vs. regex-based line filtering |
| **File** | `context_compressor.py` |

**Configuration:**

```bash
LLMLINGUA_ENABLED=true          # Enable/disable (default: true)
LLMLINGUA_MODEL=microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
LLMLINGUA_RATE=0.5              # Target compression ratio (0.3 = aggressive, 0.7 = conservative)
LLMLINGUA_MIN_LENGTH=200        # Skip compression for short texts
```

**Installation:**

```bash
pip install llmlingua>=0.2.0
```

> **Note:** LLMLingua downloads a ~400MB model on first use. In Docker, this is cached in the data volume.

**Fallback behavior:** If `llmlingua` is not installed, the original heuristic compression runs unchanged.

---

### 2. ChromaDB — Semantic Memory

| | |
|---|---|
| **Replaces** | JSON-based keyword matching in `cross_project_memory.py` |
| **Library** | [ChromaDB](https://github.com/chroma-core/chroma) |
| **Why** | Enables semantic similarity search — finds relevant lessons even without keyword overlap (e.g., "Docker networking" matches "container port mapping issues") |
| **File** | `cross_project_memory.py` (in-place upgrade) |

**Configuration:**

```bash
MEMORY_BACKEND=chroma           # "chroma" or "json" (default: chroma)
CHROMA_PERSIST_DIR=/app/data/chroma_db  # Storage location
```

**Installation:**

```bash
pip install chromadb>=0.5.0
```

**Key features:**
- Automatic embedding generation using local model (no external API needed)
- One-time automatic migration of existing JSON data to ChromaDB
- Dual-write to both ChromaDB and JSON (backup safety)
- New `semantic_search()` method for cross-cutting knowledge queries

**Fallback behavior:** If `chromadb` is not installed or `MEMORY_BACKEND=json`, the original JSON-based keyword matching is used.

---

### 3. RouteLLM — Smart Model Routing

| | |
|---|---|
| **Replaces** | Static model assignment in `agent_runtime.py` |
| **Library** | [RouteLLM (LMSYS)](https://github.com/lm-sys/RouteLLM) |
| **Why** | Dynamically routes between expensive (Sonnet) and cheap (Haiku) models based on task complexity, saving 40-60% on API costs |
| **File** | `smart_router.py` |

**Configuration:**

```bash
SMART_ROUTER_ENABLED=true       # Enable/disable (default: true)
SMART_ROUTER_BACKEND=builtin    # "routellm" or "builtin" (default: builtin)
SMART_ROUTER_THRESHOLD=0.5      # Complexity threshold (0.0-1.0)
STRONG_MODEL=claude-sonnet-4-20250514
WEAK_MODEL=claude-haiku-3-5-20241022
ROUTER_ALWAYS_STRONG=architect,tech_lead,pm  # Roles that always use strong model
```

**Installation:**

```bash
pip install routellm>=0.1.0
```

**How it works:**
1. Before each agent task, `route_model_for_task(role, prompt)` is called
2. If RouteLLM is installed, it uses a trained matrix-factorization classifier
3. If not, a built-in pattern-based classifier analyzes the prompt
4. Critical roles (architect, PM, tech lead) always use the strong model
5. The selected model is passed to Claude Code CLI via the `--model` flag

**Fallback behavior:** If `routellm` is not installed, the built-in classifier (pattern matching + role heuristics) is used. If routing is disabled entirely (`SMART_ROUTER_ENABLED=false`), the strong model is always used.

---

### 4. Firejail — Process Sandboxing

| | |
|---|---|
| **Replaces** | No existing isolation (agents run with full user permissions) |
| **Library** | [Firejail](https://github.com/netblue30/firejail) |
| **Why** | Adds OS-level security isolation using Linux namespaces and seccomp-bpf |
| **File** | `sandbox_guard.py` |

**Configuration:**

```bash
SANDBOX_ENABLED=true            # Enable/disable (default: true)
SANDBOX_BACKEND=firejail        # "firejail" or "none" (default: firejail)
SANDBOX_ALLOW_NETWORK=false     # Allow network access (default: false)
SANDBOX_EXTRA_ARGS=[]           # Additional Firejail args (JSON list)
```

**Installation:**

```bash
# System package (not pip)
sudo apt-get install firejail

# Docker: Already included in the Dockerfile
```

**Security restrictions applied:**
- Filesystem: Only the project working directory is writable
- Network: Blocked by default (configurable)
- Sensitive files: SSH keys, AWS credentials, shadow file are blacklisted
- Process limits: Max 100 processes, 1024 file descriptors
- Capabilities: All dropped, no privilege escalation
- Seccomp: System call filtering enabled

**Fallback behavior:** If Firejail is not installed, commands run without sandboxing (original behavior).

---

## Quick Start

### Minimal (fallback mode — no new dependencies)

Everything works out of the box. The new modules detect missing libraries and fall back to the original behavior:

```bash
# No changes needed — just pull the latest code
git pull
pip install -r requirements.txt  # Only core deps are required
```

### Full (all OSS integrations)

```bash
# Python packages
pip install llmlingua chromadb routellm

# System package (Linux only)
sudo apt-get install firejail

# Or with Docker (everything included)
docker-compose up --build
```

### Selective

Enable only what you need:

```bash
# Only semantic memory
pip install chromadb
echo "MEMORY_BACKEND=chroma" >> .env

# Only smart routing (built-in classifier, no extra deps)
echo "SMART_ROUTER_ENABLED=true" >> .env

# Only context compression
pip install llmlingua
echo "LLMLINGUA_ENABLED=true" >> .env
```

---

## Disabling Features

All features can be disabled via environment variables:

```bash
LLMLINGUA_ENABLED=false
MEMORY_BACKEND=json
SMART_ROUTER_ENABLED=false
SANDBOX_ENABLED=false
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    DAG Executor                          │
│                 (orchestrator.py)                        │
│                    KEEP AS-IS                            │
└──────────┬──────────────┬──────────────┬────────────────┘
           │              │              │
    ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼──────┐
    │  Smart      │ │  Context  │ │  Semantic  │
    │  Router     │ │ Compressor│ │  Memory    │
    │ (RouteLLM)  │ │(LLMLingua)│ │ (ChromaDB) │
    │  NEW OSS    │ │  NEW OSS  │ │  NEW OSS   │
    └──────┬──────┘ └─────┬─────┘ └─────┬──────┘
           │              │              │
    ┌──────▼──────────────▼──────────────▼────────────────┐
    │              Claude Code CLI (subprocess)            │
    │              isolated_query.py                       │
    │              ┌──────────────────┐                    │
    │              │  Firejail Guard  │                    │
    │              │    NEW OSS       │                    │
    │              └──────────────────┘                    │
    └─────────────────────────────────────────────────────┘
```

---

## Monitoring

Each module exposes a status endpoint for diagnostics:

```python
from smart_router import get_router_status
from sandbox_guard import get_sandbox_status
from cross_project_memory import CrossProjectMemory

# Router status
print(get_router_status())
# {'enabled': True, 'backend': 'builtin', 'active_backend': 'builtin', ...}

# Sandbox status
print(get_sandbox_status())
# {'enabled': True, 'firejail_path': '/usr/bin/firejail', 'available': True, ...}

# Memory status
mem = CrossProjectMemory("/app/data")
print(mem.stats)
# {'lessons': 42, 'tech_patterns': 8, 'backend': 'chroma', 'chroma_documents': 50}
```
