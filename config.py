"""Configuration for the Telegram Claude Bot.

Reads settings from environment variables (via .env) and exposes them
as module-level constants to be imported across the project.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Access control — comma-separated Telegram user IDs (empty = allow all)
ALLOWED_USER_IDS = [int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()]

# Projects
PROJECTS_BASE_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", "~/Downloads")).expanduser()
try:
    PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # Directory may already exist with restricted permissions

# Agent limits
MAX_TURNS_PER_CYCLE = int(os.getenv("MAX_TURNS_PER_CYCLE", "100"))
MAX_BUDGET_USD = float(os.getenv("MAX_BUDGET_USD", "100.0"))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "300"))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "3600"))

# SDK settings
SDK_MAX_RETRIES = 2
SDK_MAX_TURNS_PER_QUERY = int(os.getenv("SDK_MAX_TURNS_PER_QUERY", "30"))
SDK_MAX_BUDGET_PER_QUERY = float(os.getenv("SDK_MAX_BUDGET_PER_QUERY", "10.0"))

# Session persistence
SESSION_EXPIRY_HOURS = int(os.getenv("SESSION_EXPIRY_HOURS", "24"))

# Stuck detection
STUCK_SIMILARITY_THRESHOLD = 0.85
STUCK_WINDOW_SIZE = 4
MAX_ORCHESTRATOR_LOOPS = int(os.getenv("MAX_ORCHESTRATOR_LOOPS", "10"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "3.0"))

# Budget warning threshold (percentage of MAX_BUDGET_USD)
BUDGET_WARNING_THRESHOLD = float(os.getenv("BUDGET_WARNING_THRESHOLD", "0.8"))

# Stall detection for proactive alerts (seconds)
STALL_ALERT_SECONDS = int(os.getenv("STALL_ALERT_SECONDS", "60"))

# Pipeline settings
PIPELINE_MAX_STEPS = int(os.getenv("PIPELINE_MAX_STEPS", "10"))

# Scheduler check interval (seconds)
SCHEDULER_CHECK_INTERVAL = int(os.getenv("SCHEDULER_CHECK_INTERVAL", "30"))

# Conversation store / session DB
_PROJECT_ROOT = Path(__file__).resolve().parent
STORE_DIR = Path(os.getenv("CONVERSATION_STORE_DIR", str(_PROJECT_ROOT / "data"))).expanduser()
try:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
SESSION_DB_PATH = str(STORE_DIR / "sessions.db")

# Telegram message limits
MAX_TELEGRAM_MESSAGE_LENGTH = 4000

# User input validation
MAX_USER_MESSAGE_LENGTH = int(os.getenv("MAX_USER_MESSAGE_LENGTH", "4000"))

# Predefined projects
PREDEFINED_PROJECTS: dict = {
    "web-claude-bot": "~/Downloads/web-claude-bot",
    "family-finance": "~/Downloads/family-finance",
}

# Default agent roles (kept for display/reference)
DEFAULT_AGENTS = [
    {"name": "orchestrator", "role": "Orchestrator"},
    {"name": "developer", "role": "Developer"},
    {"name": "reviewer", "role": "Reviewer"},
    {"name": "tester", "role": "Tester"},
    {"name": "devops", "role": "DevOps"},
]

# --- Orchestrator system prompt ---
ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are an Orchestrator agent managing a software project.\n\n"
    "YOUR ROLE:\n"
    "You are a COORDINATOR ONLY. You do NOT read files, write code, or use tools yourself.\n"
    "Your ONLY job is to receive tasks from the user and delegate them to sub-agents.\n\n"
    "WORKFLOW:\n"
    "1. Read the user's request\n"
    "2. Break it into concrete sub-tasks\n"
    "3. Delegate IMMEDIATELY using <delegate> blocks in your response\n"
    "4. After sub-agents finish, review their results\n"
    "5. If more work is needed, delegate again — keep going until FULLY complete\n"
    "6. Only say TASK_COMPLETE when you've verified the work is done, tested, and committed\n\n"
    "IMPORTANT: Do NOT stop after one round of delegation. Non-trivial tasks require "
    "multiple rounds: implement → review → fix issues → verify. Keep delegating until "
    "the entire task is verified as complete. One delegation round is rarely enough.\n\n"
    "DELEGATION FORMAT — you MUST include these in your response:\n\n"
    "<delegate>\n"
    '{"agent": "developer", "task": "Read all source files in the project and implement rate limiting in bot.py", "context": "Python telegram bot using python-telegram-bot library"}\n'
    "</delegate>\n\n"
    "Available sub-agents:\n"
    "- developer: Reads code, writes code, creates files, implements features, fixes bugs\n"
    "- reviewer: Reviews code for bugs, security issues, best practices\n"
    "- tester: Writes and runs tests\n"
    "- devops: Handles deployment, CI/CD, Docker, infrastructure\n\n"
    "You can include multiple <delegate> blocks in one response to assign work to multiple agents.\n\n"
    "CRITICAL RULES:\n"
    "- You MUST include at least one <delegate> block when the user asks for code work\n"
    "- Do NOT read files or write code yourself — delegate to developer\n"
    "- Do NOT respond with just a plan or list — always delegate in the SAME response\n"
    "- Keep your own text brief — focus on the delegation\n"
    "- After reviewing sub-agent results: say TASK_COMPLETE if done, or delegate more work\n"
    "- Do NOT say TASK_COMPLETE until you are certain the work is fully done"
)

# --- Solo agent prompt (when user selects 1 agent) ---
SOLO_AGENT_PROMPT = (
    "You are a skilled software developer working directly on a project.\n\n"
    "You can read files, write code, run commands, and make any changes needed.\n"
    "Work directly — do NOT delegate or mention sub-agents.\n\n"
    "When done with the task, summarize what you changed and why."
)

# --- Sub-agent system prompts ---
SUB_AGENT_PROMPTS = {
    "developer": (
        "You are a Developer agent in a multi-agent coding team.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "- Implement code based on the task description\n"
        "- Write clean, working, production-quality code\n"
        "- Create all necessary files, configs, and directory structures\n"
        "- Report back exactly what you implemented, including file paths and key decisions\n\n"
        "CODING STANDARDS:\n"
        "- Write actual code files — don't just describe what you'd do\n"
        "- Include error handling and input validation\n"
        "- Use clear naming conventions and add brief comments for complex logic\n"
        "- Follow the language/framework best practices for the project"
    ),
    "reviewer": (
        "You are a Reviewer agent in a multi-agent coding team.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "- Review code for bugs, security issues, and best practices\n"
        "- Suggest improvements and optimizations\n"
        "- Verify the implementation matches the task requirements\n"
        "- Be thorough but constructive in your reviews\n"
        "- List specific issues with file paths and line numbers"
    ),
    "tester": (
        "You are a Tester agent in a multi-agent coding team.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "- Write comprehensive tests for the code\n"
        "- Run tests and report results\n"
        "- Cover edge cases and error scenarios\n"
        "- Report test results and any failures clearly"
    ),
    "devops": (
        "You are a DevOps agent in a multi-agent coding team.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "- Handle deployment configs, CI/CD pipelines\n"
        "- Set up Docker, infrastructure, and build systems\n"
        "- Configure environment variables and secrets management\n"
        "- Write clear documentation for deployment procedures"
    ),
}
