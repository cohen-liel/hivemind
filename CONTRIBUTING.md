# Contributing to Hivemind

Thank you for your interest in contributing to Hivemind! This document provides guidelines and instructions for contributing.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment for everyone.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/cohen-liel/hivemind/issues) to avoid duplicates.
2. Use the **Bug Report** issue template.
3. Include steps to reproduce, expected behavior, and actual behavior.
4. Include your OS, Python version, and Node.js version.

### Suggesting Features

1. Open a **Feature Request** issue.
2. Describe the use case and why it would be valuable.
3. If possible, suggest an implementation approach.

### Submitting Pull Requests

1. Fork the repository.
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. Make your changes following the code style guidelines below.
4. Test your changes thoroughly locally.
5. Commit with clear, descriptive messages following Conventional Commits:
   ```bash
   git commit -m "feat: add project health monitoring endpoint"
   ```
6. Push and open a Pull Request against `main`.

### Pull Request Process & Code Review

1. **Continuous Integration**: Once you open a PR, our CI pipeline will automatically run linting and tests. Ensure all checks pass.
2. **Review Process**: Every PR requires at least one approval from a maintainer before it can be merged.
3. **Addressing Feedback**: Be open to feedback and ready to make adjustments. We value constructive code reviews!
4. **Merging**: Once approved and all checks pass, a maintainer will merge your PR (usually via Squash and Merge).

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- Claude Code CLI (`claude` command available)

### Local Development

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/hivemind.git
cd hivemind

# Run the setup script (installs everything)
chmod +x setup.sh
./setup.sh

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Start the development server
./restart.sh
```

### Project Structure

```
hivemind/
├── server.py              # Main entry point, HTTP server, tunnel setup
├── orchestrator.py        # Core orchestration engine (multi-agent coordination)
├── dag_executor.py        # DAG-based parallel task execution
├── pm_agent.py            # Project Manager agent (task planning)
├── config.py              # Configuration, prompts, agent registry
├── contracts.py           # Data contracts (TaskInput, Artifact, etc.)
├── device_auth.py         # Device token authentication system
├── project_context.py     # Project context builder and isolation
├── state.py               # State management
├── sdk_client.py          # Claude Code SDK client
├── dashboard/
│   └── api.py             # FastAPI dashboard API endpoints
├── frontend/
│   ├── src/
│   │   ├── App.tsx        # Main app with auth gate
│   │   ├── components/    # React components
│   │   ├── pages/         # Page components (Dashboard, ProjectView, Settings)
│   │   └── types.ts       # TypeScript type definitions
│   └── vite.config.ts     # Vite build configuration
├── tests/                 # Test suite
├── docs/                  # Internal documentation
└── setup.sh               # One-click installation script
```

### Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_contracts.py -v

# Run with coverage
python3 -m pytest tests/ --cov=. --cov-report=html
```

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Development mode (hot reload)
npm run dev

# Type checking
npx tsc --noEmit

# Production build
npx vite build
```

## Code Style Guidelines

### Python

- Follow PEP 8 conventions.
- Use type hints for function signatures.
- Add docstrings to public functions and classes.
- Keep functions focused and under 50 lines when possible.
- Use `async/await` for I/O-bound operations.

### TypeScript / React

- Use functional components with hooks.
- Define types in `types.ts` for shared interfaces.
- Use descriptive variable and component names.
- Keep components focused on a single responsibility.

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Use |
|--------|-----|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation changes |
| `refactor:` | Code refactoring |
| `test:` | Adding or updating tests |
| `chore:` | Build, CI, or tooling changes |

## Architecture Overview

Hivemind uses a **multi-agent DAG architecture**:

1. **User Request** arrives via the web dashboard.
2. **PM Agent** analyzes the request and creates a task DAG (Directed Acyclic Graph).
3. **DAG Executor** runs tasks in parallel where possible, using specialist agents.
4. **Specialist Agents** (coder, reviewer, architect, etc.) execute individual tasks via Claude Code.
5. **Orchestrator** coordinates everything, manages state, and reports progress back to the dashboard.

Each project has strict **file scope isolation** — agents can only read/write files within their assigned project directory.

## AI-Generated Contributions

We welcome contributions that use AI tools (GitHub Copilot, Claude, ChatGPT, etc.) to assist with development. However, we require **full transparency**:

1. **Disclosure Required.** If your PR was substantially generated by AI, you **must** disclose this in the PR description. Use the label `ai-generated` or `ai-assisted`.
2. **You Own It.** Submitting a PR means you have reviewed, tested, and take responsibility for the code — regardless of how it was produced.
3. **No Bulk AI PRs.** Do not submit large, untested, AI-generated PRs that dump code without context. Every PR must include a clear description of what it does and why.
4. **Quality Standards Apply.** AI-generated code must meet the same code style, testing, and review standards as human-written code.
5. **Automated Bot PRs.** Fully automated PRs from bots (without human review) will be closed immediately.

We believe AI is a powerful tool for developers. We just ask that you use it responsibly and transparently.

## Creating a New Agent

1. Add the agent to `AGENT_REGISTRY` in `config.py`:

```python
"my_agent": AgentConfig(
    timeout=900,
    turns=100,
    budget=50.0,
    layer="execution",  # or "quality"
    emoji="\U0001f4a1",
    label="My Agent",
    tw_color="blue",
    accent="#638cff",
),
```

2. Add the role to `AgentRole` enum in `contracts.py`
3. Add a specialist prompt in `config.py` → `SPECIALIST_PROMPTS`
4. Add a description in `pm_agent.py` → `_ROLE_DESCRIPTIONS`
5. Update the org hierarchy in `org_hierarchy.py` if needed

## Creating a New Runtime

See `agent_runtime.py` for the runtime abstraction layer. Hivemind supports multiple runtimes:

| Runtime | Description |
|---------|-------------|
| Claude Code | Default — Anthropic Claude Code SDK |
| OpenClaw | Open-source AI agent framework |
| Bash | Direct shell command execution |
| HTTP | External API calls |

To add a new runtime, implement the `AgentRuntime` interface and register it in `RuntimeRegistry`.

## Organizational Hierarchy

Each project has a corporate management structure:

```
CEO (Orchestrator)
├── CTO (PM)
│   ├── VP Engineering → Frontend, Backend, Database
│   ├── VP Quality → Tester, Security, Reviewer
│   └── VP Research → Researcher, UX
└── VP Operations → DevOps
```

See `org_hierarchy.py` for the full implementation.

## Areas Where Help is Needed

- **Agent Runtimes**: Adding support for more AI providers (Gemini, GPT, local models)
- **Project Templates**: New templates (e-commerce, blog, mobile app, CLI tool)
- **Testing**: Expanding test coverage, especially for the DAG executor and orchestrator
- **Documentation**: Improving inline code documentation and user guides
- **Frontend**: UI/UX improvements, accessibility, mobile responsiveness
- **Dashboard**: New visualizations, org chart improvements, analytics
- **Performance**: Optimizing the orchestrator for large projects with many parallel tasks

## Questions?

Open a [Discussion](https://github.com/cohen-liel/hivemind/discussions) or reach out via Issues.

Thank you for helping make Hivemind better!
