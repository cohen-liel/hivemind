<div align="center">

# Hivemind

### One prompt. A full team of AI agents. Production-ready code.

[![CI](https://github.com/cohen-liel/hivemind/actions/workflows/ci.yml/badge.svg)](https://github.com/cohen-liel/hivemind/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178C6.svg)](https://typescriptlang.org)
[![Claude Code](https://img.shields.io/badge/Claude_Code-SDK-orange.svg)](https://docs.anthropic.com/en/docs/claude-code)

**Give Hivemind a task. It deploys a PM, developers, reviewer, and QA agent — all working in parallel — and delivers tested, committed code in minutes.**

[Quick Start](#-quick-start) | [How It Works](#-how-it-works) | [Architecture](#-architecture) | [Dashboard](#-dashboard) | [Contributing](CONTRIBUTING.md)

</div>

---

<div align="center">

### Desktop Dashboard
![Hivemind Dashboard — Desktop](docs/screenshots/dashboard-desktop.png)

</div>

<div align="center">
<table>
<tr>
<td align="center"><strong>Mobile Dashboard</strong></td>
<td align="center"><strong>Mobile Project View</strong></td>
</tr>
<tr>
<td><img src="docs/screenshots/dashboard-mobile.png" width="300" alt="Hivemind — Mobile Dashboard" /></td>
<td><img src="docs/screenshots/project-mobile.png" width="300" alt="Hivemind — Mobile Project View" /></td>
</tr>
</table>
</div>

---

## The Problem

You ask Claude Code to build a feature. It works on one file at a time, loses context, and you end up babysitting the process for hours. For anything beyond a simple script, you become the project manager, the code reviewer, and the QA team — all at once.

## The Solution

Hivemind turns Claude Code into a **software engineering team**. You describe what you want in plain language. Hivemind's PM agent breaks it into a dependency-aware task graph, then deploys specialist agents — frontend developer, backend developer, database expert, security auditor, test engineer — that execute in parallel, pass typed artifacts between each other, and self-heal when something fails.

The result: complex features built, reviewed, tested, and committed autonomously.

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://python.org) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Claude Code CLI | Latest | See below |

**Install Claude Code CLI:**

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

### Option 1: Quick Install (Recommended)

```bash
# Clone the repository
git clone https://github.com/cohen-liel/hivemind.git
cd hivemind

# Run the setup script (installs all dependencies + builds frontend)
chmod +x setup.sh restart.sh
./setup.sh

# Configure your projects directory
cp .env.example .env
# Edit .env and set CLAUDE_PROJECTS_DIR to your projects folder
# Example: CLAUDE_PROJECTS_DIR=/Users/yourname/projects

# Launch Hivemind
./restart.sh
```

Open **http://localhost:8080** in your browser. That's it.

### Option 2: Docker

```bash
docker-compose up -d --build
```

### Option 3: Manual Install

```bash
# Clone
git clone https://github.com/cohen-liel/hivemind.git
cd hivemind

# Python dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
npm install
npm run build
cd ..

# Configure
cp .env.example .env
# Edit .env — set CLAUDE_PROJECTS_DIR

# Run
python3 server.py
```

### First Launch

1. Open **http://localhost:8080** in your browser
2. If device authentication is enabled, enter the **access code** shown in your terminal
3. Click **"+ New Project"** in the sidebar
4. Select a working directory (the folder containing your code)
5. Choose an agent configuration: **Solo**, **Team**, or **Full Team**
6. Type a task in the message box and hit **Execute**

---

## ⚡ How It Works

When you send a message to Hivemind, here is what happens behind the scenes:

```
You: "Add user authentication with JWT tokens and a login page"
                    │
                    ▼
         ┌──────────────────┐
         │    PM Agent       │  Step 1: Analyzes request, creates TaskGraph
         │    (Planning)     │  with dependencies and file scopes
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │   DAG Executor    │  Step 2: Launches agents in parallel
         │   (Orchestration) │  where dependencies allow
         └────────┬─────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
┌────────┐  ┌────────┐  ┌────────┐
│Backend │  │Frontend│  │Database│   Step 3: Agents work in parallel,
│  Dev   │  │  Dev   │  │ Expert │   passing typed artifacts downstream
└───┬────┘  └───┬────┘  └───┬────┘
    │           │           │
    └─────────┬─┘───────────┘
              ▼
    ┌──────────────────┐
    │   Test Engineer   │   Step 4: Tests the combined output
    └────────┬─────────┘
             ▼
    ┌──────────────────┐
    │    Reviewer       │   Step 5: Quality gate — checks correctness,
    │  (Code Review)    │   consistency, and code quality
    └────────┬─────────┘
             ▼
        ✅ Committed
```

**Step 1 — Planning.** The PM Agent analyzes your request and produces a structured **Task Graph** (DAG) with dependencies, file scopes, and agent assignments.

**Step 2 — Parallel Execution.** The DAG Executor launches specialist agents in parallel where dependencies allow. Each agent receives a typed task input with the exact files it may touch.

**Step 3 — Artifact Flow.** When an agent completes its task, it produces a structured artifact (API contract, schema, test report) that flows to downstream agents as context.

**Step 4 — Self-Healing.** If a task fails, the system classifies the failure and spawns a targeted remediation task — not a blind retry.

**Step 5 — Quality Gate.** A reviewer agent checks the combined output for correctness, consistency, and code quality before the final commit.

---

## 🏗️ Architecture

Hivemind is built in three layers:

| Layer | Components | Responsibility |
|---|---|---|
| **Dashboard** | React + FastAPI + WebSocket | Real-time UI, REST API, device authentication |
| **Orchestration** | Orchestrator + PM Agent + DAG Executor | Task planning, parallel execution, state management, memory |
| **Execution** | Specialist Agents + Claude Code SDK | Code generation, review, debugging, testing via Claude Code CLI |

### Technical Highlights

| Feature | What It Does |
|---|---|
| **Dependency-Aware DAG** | Tasks execute in the optimal order. Independent tasks run in parallel; dependent tasks wait for upstream artifacts. |
| **Self-Healing Execution** | Failed tasks are classified by failure type and retried with targeted fixes, not blind restarts. |
| **Proactive Memory** | The orchestrator injects lessons learned from past sessions to prevent repeating the same mistakes. |
| **Two-Phase Agent Protocol** | Each agent runs a work phase (tools enabled) followed by a structured summary phase, guaranteeing parseable output. |
| **Smart Concurrency Control** | Reader agents run in parallel; writer agents are serialized when their file scopes overlap to prevent conflicts. |
| **Project Isolation** | Every agent is sandboxed to its project directory. Cross-project file access is blocked at multiple enforcement layers. |
| **Circuit Breaker** | The SDK client implements a circuit breaker pattern to prevent cascade failures when Claude Code is overloaded. |
| **Device Authentication** | Zero-password auth. Approve devices with a rotating access code + optional QR scan from your phone. Multiple devices can connect with the same code. |

---

## 🤖 Agent Roster

Hivemind deploys the right agent for each task. Here is the full team:

### Planning and Coordination

| Agent | Role |
|---|---|
| **PM Agent** | Analyzes the request and creates the structured execution plan (TaskGraph) |
| **Orchestrator** | Routes messages, manages delegation, tracks progress, handles lifecycle |
| **Memory Agent** | Updates project knowledge after each execution to improve future runs |

### Development

| Agent | Specialty |
|---|---|
| **Frontend Developer** | React, TypeScript, Tailwind, state management |
| **Backend Developer** | FastAPI, async Python, REST APIs, WebSockets |
| **Database Expert** | Schema design, query optimization, migrations |
| **DevOps** | Docker, CI/CD, deployment, environment configuration |
| **TypeScript Architect** | Advanced TypeScript patterns, generics, design systems |

### Quality Assurance

| Agent | Specialty |
|---|---|
| **Test Engineer** | pytest, TDD, end-to-end tests |
| **Security Auditor** | OWASP Top 10, dependency scanning |
| **Reviewer** | Code quality, architecture critique, consistency checks |
| **UX Critic** | Accessibility, usability heuristics |
| **Researcher** | Technical research, documentation, best practices |

---

## 📊 Dashboard

<div align="center">

![Hivemind Agents View](docs/screenshots/agents-desktop.png)

</div>

The web dashboard provides full visibility into what every agent is doing:

- **Live Agent Output** — Stream each agent's work in real-time via WebSocket
- **DAG Progress** — Visual task graph showing agent status and dependencies
- **Agent Cards** — See all 11 agents with their current status (Standby, Working, Done)
- **Plan View** — Live execution plan with ✓ completion tracking, collapsible completed tasks, and progress bar
- **Code Browser** — Browse and diff the files agents are creating and modifying
- **Diff View** — See exactly what changed in each file
- **Cost Analytics** — Monitor token usage and cost per session over time
- **Schedules** — Set up recurring tasks with cron expressions
- **Dark/Light Mode** — Full theme support
- **Mobile Optimized** — WhatsApp-like auto-expanding input, bottom tab nav, haptic feedback, "New messages" pill for quick scroll-to-bottom

<div align="center">

![New Project Dialog](docs/screenshots/new-project-desktop.png)

</div>

---

## 📱 Remote Access

Access Hivemind from your phone, tablet, or any device:

```bash
# Set host to 0.0.0.0 in .env
DASHBOARD_HOST=0.0.0.0
```

Start the server and it prints everything you need — local URL, public URL, access code, and a **QR code** you can scan with your phone:

```
  ╔══════════════════════════════════════════════════════╗
  ║              ⚡ Hivemind is running                  ║
  ╠══════════════════════════════════════════════════════╣
  ║  🌐 Local:   http://localhost:8080                   ║
  ║  🏠 Network: http://192.168.1.42:8080                ║
  ║  🌍 Public:  https://random-name.trycloudflare.com   ║
  ╠══════════════════════════════════════════════════════╣
  ║  🔑 Access Code:  A3K7NP2Q                           ║
  ╠══════════════════════════════════════════════════════╣
  ║  📱 Scan QR to open on your phone:                   ║
  ║       ████████████████                               ║
  ╚══════════════════════════════════════════════════════╝
```

When you open the dashboard from a new device, enter the **access code** shown in your terminal. The device is approved permanently — no passwords, no accounts. The code rotates every 5 minutes and supports **multiple devices** connecting with the same code.

For extra security, set `HIVEMIND_PASSWORD` in `.env` — devices will need both the code and the password.

For a permanent URL, set up a named Cloudflare Tunnel:

```bash
cloudflared tunnel login
cloudflared tunnel create hivemind
cloudflared tunnel route dns hivemind your-domain.com
```

---

## ⚙️ Configuration

All configuration is done via environment variables in `.env`:

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_CLI_PATH` | `claude` | Path to Claude CLI binary |
| `CLAUDE_PROJECTS_DIR` | `~/claude-projects` | Base directory for project workspaces |
| `DASHBOARD_PORT` | `8080` | Dashboard listen port |
| `DASHBOARD_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for remote access) |

### Agent Limits

| Variable | Default | Description |
|---|---|---|
| `MAX_TURNS_PER_CYCLE` | `200` | Maximum turns before pausing |
| `MAX_BUDGET_USD` | `100` | Budget limit per session in USD |
| `AGENT_TIMEOUT_SEC` | `900` | Timeout for each agent query in seconds |
| `MAX_ORCHESTRATOR_LOOPS` | `100` | Safety limit on orchestrator iterations |

### SDK Settings

| Variable | Default | Description |
|---|---|---|
| `SDK_MAX_TURNS_PER_QUERY` | `200` | Turns per sub-agent query |
| `SDK_MAX_BUDGET_PER_QUERY` | `50` | Budget per sub-agent query in USD |

### Security

| Variable | Default | Description |
|---|---|---|
| `DEVICE_AUTH_ENABLED` | `true` | Enable device-based authentication |
| `HIVEMIND_PASSWORD` | *(empty)* | Optional password required alongside the access code |
| `SANDBOX_ENABLED` | `true` | Restrict agents to project directories |
| `SESSION_EXPIRY_HOURS` | `24` | Session expiry time |

---

## 🛠️ Development

```bash
# Frontend development with hot reload
cd frontend && npm run dev

# Run the test suite
python3 -m pytest tests/ -v

# Type checking
cd frontend && npx tsc --noEmit

# Lint + format
ruff check .
ruff format --check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.

### Project Structure

```
hivemind/
├── server.py              # FastAPI entry point + Cloudflare tunnel setup
├── orchestrator.py        # Core orchestration engine
├── dag_executor.py        # Parallel DAG execution engine
├── pm_agent.py            # Project Manager — creates TaskGraph
├── contracts.py           # Typed agent protocol (TaskInput → TaskOutput)
├── config.py              # Agent prompts, registry, operational constants
├── device_auth.py         # Device token authentication
├── _shared_utils.py       # Centralized utility functions
├── orch_agents.py         # Agent query and sub-agent management
├── orch_watchdog.py       # Silence watchdog and stuck detection
├── orch_context.py        # Context building for agents
├── orch_experience.py     # Experience and memory injection
├── orch_review.py         # Code review orchestration
├── project_context.py     # Project context and file management
├── dashboard/
│   └── api.py             # REST API + WebSocket + auth middleware
├── frontend/
│   └── src/               # React + TypeScript + Tailwind UI
│       ├── pages/         # Dashboard, Settings, Project views
│       └── components/    # Reusable UI components
├── terminal_qr.py         # Terminal QR code renderer (zero-dep)
├── tests/                 # 1,282 tests — unit, integration, e2e
├── setup.sh               # One-command setup script
├── restart.sh             # Server restart script
├── docker-compose.yml     # Docker deployment
├── CONTRIBUTING.md        # Contribution guidelines
├── SECURITY.md            # Security policy
├── CODE_OF_CONDUCT.md     # Community code of conduct
└── LICENSE                # Apache License 2.0
```

---

## 🔧 Troubleshooting

<details>
<summary><strong>Server won't start (port in use)</strong></summary>

```bash
lsof -ti :8080 | xargs kill -9
./restart.sh
```

</details>

<details>
<summary><strong>Claude Code CLI not found</strong></summary>

```bash
npm install -g @anthropic-ai/claude-code
claude login
# Verify: claude --version
```

</details>

<details>
<summary><strong>No public URL (Cloudflare tunnel not working)</strong></summary>

```bash
# Check if cloudflared is installed
cloudflared --version

# If not installed:
# macOS: brew install cloudflared
# Linux: sudo apt install cloudflared
# Or re-run setup: ./setup.sh
```

</details>

<details>
<summary><strong>Device authentication code not showing</strong></summary>

Check the terminal where `server.py` is running. The access code and QR code are printed on startup. The code rotates every 5 minutes — if it expired, a new one is generated automatically. You can also rotate the code from the Settings page in the dashboard.

</details>

<details>
<summary><strong>Agents not starting / "No Claude CLI found"</strong></summary>

Make sure Claude Code CLI is installed and authenticated:

```bash
which claude          # Should return a path
claude --version      # Should print version
claude login          # Re-authenticate if needed
```

</details>

---

## ⚖️ License & Enterprise

### Open Source

Hivemind is proudly open-source under the **[Apache License 2.0](LICENSE)**. You are free to use, modify, and distribute the software for personal and commercial purposes.

### Hivemind for Teams (Enterprise)

While the core orchestrator will always remain open-source, we are developing advanced features for engineering organizations:

- **Centralized Agent Governance:** Manage tokens and permissions across large teams.
- **Advanced Security Auditing:** Enhanced SOC2-compliant logging for AI-generated code.
- **Custom MCP Integrations:** Private agent skills tailored to your internal stack.
- **Priority Support & SLA:** Dedicated support for mission-critical deployments.

Interested in Hivemind for your company? [Open an issue](https://github.com/cohen-liel/hivemind/issues) or reach out.

## 🔒 Security

Found a vulnerability? Please see our [Security Policy](SECURITY.md) for responsible disclosure guidelines.

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to get started, our PR process, and coding standards.
