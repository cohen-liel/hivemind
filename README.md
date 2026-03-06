# Telegram Claude Bot

A Telegram bot that acts as an orchestration layer for multiple Claude Code agents, allowing you to run parallel multi-agent software development projects.

## Features
- **Multi-Agent Teams:** Creates customizable teams of Claude agents (Architect, Developer, Reviewer, etc.).
- **Parallel Projects:** Create and switch between multiple active projects without losing context.
- **Persistent State:** Saves conversation logs and project status, allowing you to pause, stop, and resume projects at will.
- **Predefined Workspaces:** Connects directly to existing directories (e.g. `telegram-claude-bot`, `y-finance`).

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/telegram-claude-bot.git
   cd telegram-claude-bot
   ```

2. **Environment Variables:**
   Copy the example config and add your Telegram bot token:
   ```bash
   cp .env.example .env
   # Edit .env and paste your TELEGRAM_BOT_TOKEN
   ```

3. **Running Locally (Virtual Environment):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python bot.py
   ```

4. **Running via Docker (Recommended for server deployment):**
   ```bash
   docker-compose up -d --build
   ```
   *Note: Ensure your predefined paths map properly into the container as defined in `docker-compose.yml`.*

## Running Tests

To run the unit tests, simply invoke `pytest` with the root directory in your PYTHONPATH:
```bash
PYTHONPATH=. pytest tests/
```

## Available Bot Commands

- `/new` — Start a new agentic project
- `/projects` — List active, saved, and predefined projects
- `/switch <name>` — Set an active or predefined project as your current focus
- `/status` — View the budget, turns, and agent status for the active project
- `/talk <agent> <msg>` — Inject a message manually to a specific agent
- `/pause` — Pause the active project's agent cycle
- `/resume` — Resume a paused project
- `/stop` — Safely stop the agents for the current project
- `/log` — Read recent message updates from the agents
