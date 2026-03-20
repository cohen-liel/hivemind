<div align="center">

<img src="docs/hero-architecture.png" alt="Hivemind" width="100%" />

# 🧠 Hivemind

### One prompt. A full AI engineering team. Go lie on the couch.

[![GitHub stars](https://img.shields.io/github/stars/cohen-liel/hivemind?style=social)](https://github.com/cohen-liel/hivemind/stargazers)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178C6.svg)](https://typescriptlang.org)
[![Claude Code](https://img.shields.io/badge/Claude_Code-SDK-orange.svg)](https://docs.anthropic.com/en/docs/claude-code)
[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-red.svg)](https://github.com/openclaw/openclaw)
[![CI](https://github.com/cohen-liel/hivemind/actions/workflows/ci.yml/badge.svg)](https://github.com/cohen-liel/hivemind/actions/workflows/ci.yml)
[![Website](https://img.shields.io/badge/Website-Live-brightgreen.svg)](https://cohen-liel.github.io/hivemind/)

**Describe a feature in plain English. Hivemind deploys a PM, developers, reviewer, and QA — all working in parallel — and delivers tested, committed code. No babysitting. No copy-pasting. No "continue".**

[Website](https://cohen-liel.github.io/hivemind/) · [Quick Start](#-quick-start) · [How It Works](#-how-it-works) · [Features](#-features) · [Dashboard](#-dashboard) · [Agent Roster](#-agent-roster) · [Templates](#-templates) · [Contributing](CONTRIBUTING.md)

</div>

---

## What is Hivemind?

# Open-source AI engineering team that builds production code while you sleep

**If Claude Code is a _developer_, Hivemind is the _engineering team_.**

Hivemind is a Python orchestrator and React dashboard that turns AI coding agents into a full software engineering team. Give it one prompt — it plans the work, spins up specialist agents in parallel, passes artifacts between them, reviews the output, and commits tested code.

It looks like a project dashboard — but under the hood it has DAG-based task planning, parallel execution, self-healing, artifact flow, code review gates, and proactive memory.

**Ship features, not prompts.**

| Step | | Example |
| --- | --- | --- |
| **01** | Describe the feature | *"Add JWT authentication with a login page and protected routes"* |
| **02** | Watch the team work | PM plans → Frontend + Backend + DB work in parallel → Tests → Review |
| **03** | Get production code | Tested, reviewed, committed. Open your IDE and it's already there. |

> **COMING SOON: Template Marketplace** — Download pre-built project DAGs and run them with one click. SaaS starters, API backends, full-stack apps — pick a template and let the team build it.

&nbsp;

| **Works with** | 🤖 Claude Code | 🦞 OpenClaw | 🧪 Codex | ⌨️ Cursor | 🐚 Bash | 🌐 HTTP |

*If it can write code, it's hired.*

&nbsp;

## Hivemind is right for you if

- ✅ You want to **describe a feature once** and get production-ready code back
- ✅ You're tired of **babysitting Claude Code** — typing "continue", fixing context loss, managing files manually
- ✅ You want **parallel execution** — frontend, backend, and tests built simultaneously
- ✅ You want a **code review gate** before anything gets committed
- ✅ You want to **monitor everything from your phone** while lying on the couch
- ✅ You want **self-healing** — when an agent fails, the system fixes it automatically
- ✅ You want **zero extra API costs** — runs on your existing Claude Code subscription

&nbsp;

## ⚡ Features

| | | |
|---|---|---|
| 🧩 **DAG-Based Planning** | Every feature is broken into a dependency-aware task graph. Independent tasks run in parallel; dependent tasks wait for upstream artifacts. | 🔄 **Self-Healing Execution** | Failed tasks are classified by failure type and retried with targeted fixes — not blind restarts. |
| 🔀 **Artifact Flow** | Agents pass typed artifacts (API contracts, schemas, test reports) to downstream agents as structured context. | 🧠 **Proactive Memory** | The orchestrator injects lessons learned from past sessions to prevent repeating the same mistakes. |
| 🛡️ **Code Review Gate** | A reviewer agent checks the combined output for correctness, consistency, and code quality before the final commit. | ⚡ **Smart Concurrency** | Reader agents run in parallel; writer agents are serialized when their file scopes overlap to prevent conflicts. |
| 💰 **Zero Extra Cost** | No API keys needed. Runs directly on your Claude Code CLI subscription. No token charges. | 🔒 **Project Isolation** | Every agent is sandboxed to its project directory. Cross-project file access is blocked at multiple enforcement layers. |
| 📱 **Mobile Dashboard** | Real-time streaming, DAG progress, file diffs, cost analytics — all from your phone. | 🔌 **Circuit Breaker** | SDK client implements circuit breaker pattern to prevent cascade failures when the LLM is overloaded. |

&nbsp;

## Problems Hivemind solves

| Without Hivemind | With Hivemind |
| --- | --- |
| ❌ You ask Claude Code to build a feature. It works on one file at a time, loses context, and you babysit for hours. | ✅ Describe the feature once. The PM breaks it into a DAG, agents build in parallel, reviewer checks quality, code is committed. |
| ❌ For a full-stack feature, you manually coordinate backend → frontend → tests → review. Copy-pasting context between sessions. | ✅ Artifact flow passes API contracts, schemas, and test reports between agents automatically. No copy-pasting. |
| ❌ An agent gets stuck in a loop. You kill it, lose context, start over. | ✅ Self-healing detects stuck agents (5 distinct signals), reassigns, simplifies, or respawns — automatically. |
| ❌ You can't leave your desk. If you walk away, the agent stops or goes off track. | ✅ Monitor from your phone. The dashboard streams everything in real-time. Walk away. Go to the couch. |
| ❌ Agents write buggy code and you only find out after merging. | ✅ Code review gate catches issues before commit. Test engineer validates. Security auditor checks OWASP Top 10. |
| ❌ You pay per token and have no idea what each agent is costing you. | ✅ Cost analytics dashboard tracks token usage per session, per agent, over time. |

&nbsp;

## Why Hivemind is special

Hivemind handles the hard orchestration details correctly.

| | |
|---|---|
| **Dependency-aware DAG execution.** | Tasks execute in optimal order. The PM creates a real dependency graph, not a flat list. |
| **Two-phase agent protocol.** | Each agent runs a work phase (tools enabled) followed by a structured summary phase, guaranteeing parseable output. |
| **Structured Handoff Protocol.** | Agents don't just finish tasks; they write detailed handoff documents explaining *what* they built, *why*, and *how* to test it for the next agent. |
| **TDD Verification Loop.** | The test engineer doesn't just write tests—they are forced to run them, fix bugs, and provide actual terminal output as proof. |
| **Two-Stage Code Review.** | The reviewer agent performs a strict two-stage check: Stage 1 for spec compliance, and Stage 2 for code quality and security. |
| **PM Brainstorming Phase.** | Before committing to a plan, the PM agent evaluates 3 different architectural approaches and documents trade-offs. |
| **Active escalation.** | Watchdog monitors 5 stuck signals (text similarity > 85%, no file progress, circular delegation). Triggers reassign → simplify → kill & respawn. |
| **Exponential backoff with circuit breaker.** | Rate limits (429) are caught per-agent with retry strategy. Other agents continue working. |
| **Proactive memory injection.** | Past failures and lessons are injected into agent prompts so the team learns across sessions. |
| **File-scope enforcement.** | Agents can only touch files assigned to their task. Cross-scope writes are blocked at the SDK layer. |
| **Typed artifact contracts.** | Agents communicate via structured `TaskInput → TaskOutput` contracts, not free-form text. |

&nbsp;

## What Hivemind is not

| | |
|---|---|
| **Not a chatbot.** | Agents have jobs, not chat windows. |
| **Not an agent framework.** | We don't tell you how to build agents. We tell you how to run an engineering team made of them. |
| **Not a workflow builder.** | No drag-and-drop pipelines. Hivemind models engineering teams — with roles, dependencies, artifacts, and quality gates. |
| **Not a single-agent tool.** | This is for teams. If you have one agent, use Claude Code directly. If you want a team — you need Hivemind. |

&nbsp;

---

## 🚀 Quick Start

### Option 1: NPX (Recommended)

```bash
npx create-hivemind@latest
```

One command. It clones the repo, installs dependencies, builds the frontend, and starts the server.

### Option 2: Git Clone

```bash
git clone https://github.com/cohen-liel/hivemind.git
cd hivemind
chmod +x setup.sh restart.sh
./setup.sh
./restart.sh
```

### Option 3: Docker

```bash
git clone https://github.com/cohen-liel/hivemind.git
cd hivemind
docker-compose up -d --build
```

> **Requirements:** Python 3.11+, Node.js 18+, Claude Code CLI (`npm install -g @anthropic-ai/claude-code && claude login`)

### First Launch

1. Open **http://localhost:8080** in your browser
2. Enter the **access code** shown in your terminal (or scan the QR code from your phone)
3. Click **"+ New Project"** → select a working directory
4. Choose your team: **Solo**, **Team**, or **Full Team**
5. Type a task and hit **Execute**

That's it. Go lie on the couch.

&nbsp;

---

## ⚡ How It Works

```
You: "Add user authentication with JWT tokens and a login page"
                    │
                    ▼
         ┌──────────────────┐
         │    PM Agent       │  Analyzes request, creates TaskGraph (DAG)
         │    (Planning)     │  with dependencies and file scopes
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │   DAG Executor    │  Launches agents in parallel
         │   (Orchestration) │  where dependencies allow
         └────────┬─────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
┌────────┐  ┌────────┐  ┌────────┐
│Backend │  │Frontend│  │Database│   Agents work in parallel,
│  Dev   │  │  Dev   │  │ Expert │   passing typed artifacts downstream
└───┬────┘  └───┬────┘  └───┬────┘
    │           │           │
    └─────────┬─┘───────────┘
              ▼
    ┌──────────────────┐
    │   Test Engineer   │   Tests the combined output
    └────────┬─────────┘
             ▼
    ┌──────────────────┐
    │    Reviewer       │   Quality gate — checks correctness,
    │  (Code Review)    │   consistency, and code quality
    └────────┬─────────┘
             ▼
        ✅ Committed & Ready
```

&nbsp;

---

## 📊 Dashboard

<div align="center">

### Desktop
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

The web dashboard gives you full visibility into what every agent is doing:

| Feature | Description |
|---|---|
| **Live Agent Output** | Stream each agent's work in real-time via WebSocket |
| **DAG Progress** | Visual task graph showing agent status and dependencies |
| **Agent Cards** | See all 11 agents with their current status (Standby, Working, Done) |
| **Plan View** | Live execution plan with ✓ completion tracking and progress bar |
| **Code Browser** | Browse and diff the files agents are creating and modifying |
| **Cost Analytics** | Monitor token usage and cost per session over time |
| **Schedules** | Set up recurring tasks with cron expressions |
| **Dark/Light Mode** | Full theme support |
| **Mobile Optimized** | WhatsApp-like input, bottom tab nav, haptic feedback |

<div align="center">

![Hivemind Agents View](docs/screenshots/agents-desktop.png)

</div>

&nbsp;

---

## 🤖 Agent Roster

Hivemind deploys the right agent for each task. Here is the full team:

### Planning & Coordination

| Agent | Role |
|---|---|
| **PM Agent** | Brainstorms architectural approaches, analyzes trade-offs, and creates the structured execution plan (TaskGraph) |
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
| **Test Engineer** | Writes tests, runs them in a strict TDD verification loop, and proves they pass |
| **Security Auditor** | OWASP Top 10, dependency scanning |
| **Reviewer** | Performs a rigorous two-stage review: spec compliance followed by code quality checks |
| **UX Critic** | Accessibility, usability heuristics |
| **Researcher** | Technical research, documentation, best practices |

&nbsp;

---

## 📱 Remote Access

Access Hivemind from your phone, tablet, or any device:

```bash
# Set host to 0.0.0.0 in .env
DASHBOARD_HOST=0.0.0.0
```

Start the server and it prints everything you need — local URL, public URL, access code, and a **QR code** you can scan:

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

Zero-password auth. Approve devices with a rotating access code + optional QR scan. Multiple devices supported.

&nbsp;

---

## ⚙️ Configuration

All configuration via `.env`:

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_CLI_PATH` | `claude` | Path to Claude CLI binary |
| `CLAUDE_PROJECTS_DIR` | `~/claude-projects` | Base directory for project workspaces |
| `DASHBOARD_PORT` | `8080` | Dashboard listen port |
| `DASHBOARD_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for remote access) |
| `MAX_BUDGET_USD` | `100` | Budget limit per session in USD |
| `DEVICE_AUTH_ENABLED` | `true` | Enable device-based authentication |
| `SANDBOX_ENABLED` | `true` | Restrict agents to project directories |

&nbsp;

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
```

</details>

<details>
<summary><strong>Agents not starting</strong></summary>

```bash
which claude          # Should return a path
claude --version      # Should print version
claude login          # Re-authenticate if needed
```

</details>

&nbsp;

---

## 🛠️ Development

```bash
pnpm dev              # Full dev (backend + frontend, watch mode)
pnpm dev:frontend     # Frontend only with hot reload
pnpm dev:backend      # Backend only

python3 -m pytest tests/ -v   # Run 1,319 tests
cd frontend && npx tsc --noEmit   # Type checking
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.

&nbsp;

---

## 🗺️ Roadmap

- 🟢 DAG-based parallel execution
- 🟢 Real-time mobile dashboard
- 🟢 Self-healing and active escalation
- 🟢 Proactive memory
- 🟢 Code review gate
- ⚪ OpenClaw agent runtime support
- 🟢 Structured agent handoff protocol
- 🟢 Strict TDD verification loop
- 🟢 Two-stage code review
- ⚪ Template marketplace (pre-built project DAGs)
- ⚪ Plugin system for custom agent types
- ⚪ Multi-project orchestration
- ⚪ Team collaboration features

&nbsp;

---

## ⚖️ License

Open source under **[Apache License 2.0](LICENSE)**. Free for personal and commercial use.

### Hivemind for Teams (Enterprise)

While the core orchestrator will always remain open-source, we are developing advanced features for engineering organizations:

- **Centralized Agent Governance** — Manage tokens and permissions across large teams
- **Advanced Security Auditing** — SOC2-compliant logging for AI-generated code
- **Custom MCP Integrations** — Private agent skills tailored to your internal stack
- **Priority Support & SLA** — Dedicated support for mission-critical deployments

Interested? [Open an issue](https://github.com/cohen-liel/hivemind/issues) or reach out.

&nbsp;

## 🔒 Security

Found a vulnerability? See our [Security Policy](SECURITY.md) for responsible disclosure guidelines.

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 💬 Community

- [GitHub Issues](https://github.com/cohen-liel/hivemind/issues) — bugs and feature requests
- [GitHub Discussions](https://github.com/cohen-liel/hivemind/discussions) — ideas and RFC

&nbsp;

---

<div align="center">

**Open source under Apache 2.0. Built for developers who want to ship features, not babysit agents.**

</div>
