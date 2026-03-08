# Nexus — Agent OS

A web-based orchestration platform for multiple Claude Code agents, allowing you to run parallel multi-agent software development projects from any browser.

## Features
- **Multi-Agent Teams:** Creates customizable teams of Claude agents (Architect, Developer, Reviewer, etc.).
- **Parallel Projects:** Create and switch between multiple active projects without losing context.
- **Persistent State:** Saves conversation logs and project status, allowing you to pause, stop, and resume projects at will.
- **Web Dashboard:** Real-time monitoring with live agent status, activity feeds, and flow visualization.
- **PWA Support:** Install as a progressive web app on mobile devices.

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/web-claude-bot.git
   cd web-claude-bot
   ```

2. **Environment Variables:**
   Copy the example config and add your API key:
   ```bash
   cp .env.example .env
   # Edit .env and paste your ANTHROPIC_API_KEY
   ```

3. **Running Locally (Virtual Environment):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cd frontend && npm install && cd ..
   ./restart.sh
   ```

4. **Running via Docker:**
   ```bash
   docker-compose up -d --build
   ```

## Quick Start

```bash
./restart.sh        # Build frontend + start server (foreground)
./restart.sh --bg   # Build frontend + start server (background)
```

Open http://localhost:8080 in your browser.

## Running Tests

```bash
PYTHONPATH=. pytest tests/
```
