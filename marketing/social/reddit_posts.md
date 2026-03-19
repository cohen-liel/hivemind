# Reddit Launch Posts

## r/LocalLLaMA

**Title**: I built an open-source multi-agent system that runs a full AI engineering team — PM, devs, tester, reviewer — from a single prompt

**Body**:

Hey everyone,

I've been working on **Hivemind** — an open-source orchestrator that coordinates multiple AI agents to work together like a real software engineering team.

**The problem I was solving**: I was using Claude Code for development, but found myself constantly babysitting it — fixing half-done work, managing context, manually coordinating between frontend and backend tasks. It felt like I was the project manager for an AI that should be managing itself.

**What Hivemind does**:
- You write one natural language prompt describing what you want built
- A PM agent decomposes it into a DAG (directed acyclic graph) of tasks
- Specialist agents (frontend dev, backend dev, database expert, tester, security auditor, reviewer) execute tasks **in parallel**
- Agents pass typed artifacts to each other (API contracts, schemas, component maps)
- Self-healing with circuit breakers — failed tasks get auto-remediated
- Real-time dashboard shows everything happening live

**Architecture highlights**:
- Corporate org structure: CEO → CTO → VPs → Specialists (each agent knows their chain of command)
- Multi-runtime: Claude Code (default), OpenClaw, Bash, HTTP — bring your own agent
- DAG execution: parallel where possible, sequential where needed
- Artifact flow: no "telephone game" — structured data between agents

**Getting started**:
```
npx create-hivemind@latest
```

**Links**:
- GitHub: https://github.com/cohen-liel/hivemind
- MIT License

Would love feedback from this community. Especially interested in:
1. What runtimes would you want supported? (Ollama? vLLM?)
2. What project templates would be useful?
3. Any architectural concerns?

---

## r/ClaudeAI

**Title**: I built an open-source orchestrator that turns Claude Code into a full engineering team — one prompt, multiple specialist agents, production-ready code

**Body**:

I love Claude Code, but I got tired of being the project manager.

So I built **Hivemind** — an open-source system that orchestrates multiple Claude Code instances as specialist agents:

- **PM Agent**: Breaks your prompt into a task DAG
- **Frontend Developer**: React, Vue, Angular, etc.
- **Backend Developer**: APIs, business logic
- **Database Expert**: Schema design, migrations
- **Test Engineer**: Unit tests, integration tests
- **Security Auditor**: Vulnerability scanning
- **Code Reviewer**: Quality gates before commit
- **DevOps**: CI/CD, deployment configs

They work **in parallel** where possible, pass typed artifacts to each other, and self-heal when something fails.

**The key insight**: Real engineering teams don't work sequentially. The frontend dev doesn't wait for the backend dev to finish. Hivemind models this with a DAG executor that maximizes parallelism.

**New in v1.0**:
- Corporate org hierarchy (CEO/CTO/VP structure)
- Multi-runtime support (not just Claude Code — also OpenClaw, Bash, HTTP)
- Project templates (SaaS starter, REST API, React dashboard, CLI tool, mobile app)
- Interactive CLI: `npx create-hivemind@latest`

GitHub: https://github.com/cohen-liel/hivemind (MIT)

Happy to answer any questions!

---

## r/MachineLearning

**Title**: [P] Hivemind: Open-source multi-agent orchestration for software engineering — DAG-based parallel execution with self-healing

**Body**:

**Hivemind** is an open-source multi-agent orchestration system designed for software engineering tasks. It coordinates specialist AI agents (PM, frontend dev, backend dev, tester, reviewer, etc.) to work together on complex projects from a single natural language prompt.

**Key technical contributions**:

1. **DAG-based task execution**: The PM agent decomposes user requests into a directed acyclic graph. The DAG executor identifies independent tasks and runs them in parallel, with dependency resolution for sequential tasks.

2. **Typed artifact flow**: Agents communicate through structured artifacts (API contracts, database schemas, component maps, test reports) rather than free text, preventing information loss in the agent chain.

3. **Self-healing with failure classification**: When an agent fails, the system classifies the failure type (syntax error, logic error, dependency issue, etc.) and generates targeted remediation tasks.

4. **Organizational hierarchy**: Each project has a corporate management structure (CEO → CTO → VPs → Specialists) that defines chain of command, decision authority, and escalation paths.

5. **Multi-runtime abstraction**: Supports multiple agent runtimes (Claude Code, OpenClaw, Bash, HTTP) through a unified interface, allowing heterogeneous agent teams.

**Stack**: Python (FastAPI) + React/TypeScript dashboard + Claude Code SDK

**Links**:
- GitHub: https://github.com/cohen-liel/hivemind
- License: MIT

---

## Hacker News

**Title**: Show HN: Hivemind – Open-source orchestrator that runs a full AI engineering team from one prompt

**Body**:

Hivemind coordinates multiple AI agents (PM, frontend dev, backend dev, tester, reviewer) to work together on software projects.

Key ideas:

- One prompt → PM creates a task DAG → agents work in parallel → code gets reviewed and committed

- Agents pass typed artifacts (API contracts, schemas) instead of free text — no "telephone game"

- Self-healing: failed tasks get classified and auto-remediated

- Corporate org structure: CEO/CTO/VP hierarchy with escalation paths

- Multi-runtime: Claude Code, OpenClaw, Bash, HTTP

Getting started: `npx create-hivemind@latest`

GitHub: https://github.com/cohen-liel/hivemind (MIT)

I built this because I was tired of babysitting AI coding agents. Happy to discuss the architecture.

---

## LinkedIn

**Title**: Introducing Hivemind: Open-Source AI Engineering Teams

**Body**:

I'm excited to share **Hivemind** — an open-source project I've been building that orchestrates multiple AI agents to work together as a complete software engineering team.

**The insight**: Today's AI coding tools are powerful, but they work alone. Real software engineering is a team sport. Hivemind models this by coordinating specialist agents — a PM, frontend developer, backend developer, tester, security auditor, and code reviewer — all working in parallel on your project.

**How it works**:
1. You describe what you want in natural language
2. A PM agent creates a task plan (DAG)
3. Specialist agents execute tasks in parallel
4. Typed artifacts flow between agents (no information loss)
5. Self-healing handles failures automatically
6. A reviewer ensures quality before commit

**What makes it different**:
- **DAG execution**: Tasks run in parallel, not sequentially
- **Corporate hierarchy**: CEO → CTO → VP structure with clear chain of command
- **Multi-runtime**: Works with Claude Code, OpenClaw, and more
- **Open source**: MIT license, contributions welcome

If you're building with AI agents, I'd love your feedback.

🔗 GitHub: https://github.com/cohen-liel/hivemind
🚀 Try it: `npx create-hivemind@latest`

#AI #OpenSource #SoftwareEngineering #DevTools #AgentAI
