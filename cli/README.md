# create-hivemind

The fastest way to set up [Hivemind](https://github.com/cohen-liel/hivemind) — the open-source AI engineering team.

## Usage

```bash
npx create-hivemind@latest
```

That's it. The wizard handles everything: cloning, installing, building, and configuring.

## What's New in 1.2.0

- **LangGraph DAG Executor** — Tasks execute via a LangGraph StateGraph with SQLite checkpointing and self-healing retry
- **Adaptive Triage** — Simple tasks skip PM + Architect and execute directly, saving tokens and latency
- **Dynamic DAG** — Send follow-up messages mid-execution; tasks are added or cancelled in the live DAG (never parallel DAGs)
- **Read-Only Code Review** — Reviewer critiques without modifying code; automated lint/format reverted if tests break
- **Project Write Lock** — Writer agents serialized per project, preventing git conflicts
- **Architect Agent** — Pre-planning codebase review produces architecture brief for better planning

## Options

```bash
# Specify install directory
npx create-hivemind@latest my-hivemind
```

## What it does

1. Checks your system (Node.js, Python, Git, Claude Code CLI)
2. Clones the Hivemind repository
3. Installs Python and Node.js dependencies
4. Builds the React frontend
5. Configures your `.env` file
6. Starts the server automatically

## Requirements

| Dependency | Version | Required |
|---|---|---|
| Node.js | 18+ | Yes |
| Python | 3.11+ | Yes |
| Git | Any | Yes |
| Claude Code CLI | Latest | Yes |
| Docker | Any | Optional |

## License

Apache-2.0
