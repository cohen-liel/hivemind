import os
from pathlib import Path


def _load_dotenv():
    """Load .env file from the same directory as this script."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not os.environ.get(key):
                os.environ[key] = value


_load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Claude CLI
CLAUDE_CLI_PATH = os.getenv("CLAUDE_CLI_PATH", "claude")

# Projects
PROJECTS_BASE_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", "~/claude-projects")).expanduser()
PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)

# Agent limits
MAX_TURNS_PER_CYCLE = int(os.getenv("MAX_TURNS_PER_CYCLE", "50"))
MAX_BUDGET_USD = float(os.getenv("MAX_BUDGET_USD", "10000.0"))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "600"))

# Stuck detection
STUCK_SIMILARITY_THRESHOLD = 0.85
STUCK_WINDOW_SIZE = 4

# Default agent roles
DEFAULT_AGENTS = [
    {
        "name": "architect",
        "role": "Architect",
        "system_prompt": (
            "You are the **Architect** agent in a multi-agent coding team.\n\n"
            "YOUR RESPONSIBILITIES:\n"
            "- Analyze the user's requirements and create a detailed technical plan\n"
            "- Break down the project into clear, actionable tasks\n"
            "- Review code and progress reports from the Developer agent\n"
            "- Provide feedback and course corrections when needed\n"
            "- When the project is fully complete and tested, respond with TASK_COMPLETE\n\n"
            "COMMUNICATION RULES:\n"
            "- You receive messages from other agents prefixed with their name and role\n"
            "- Your response will be forwarded to the next agent in the team\n"
            "- Be concise and actionable — give clear instructions, not essays\n"
            "- When giving tasks to the Developer, number them and be specific about file paths and logic\n"
            "- After the Developer reports back, review their work and either approve or request changes\n\n"
            "COMPLETION:\n"
            "- Only say TASK_COMPLETE when ALL requirements are implemented and working\n"
            "- Before completing, verify: all files created, code runs, requirements met"
        ),
    },
    {
        "name": "developer",
        "role": "Developer",
        "system_prompt": (
            "You are the **Developer** agent in a multi-agent coding team.\n\n"
            "YOUR RESPONSIBILITIES:\n"
            "- Implement code based on the Architect's plans and instructions\n"
            "- Write clean, working, production-quality code\n"
            "- Create all necessary files, configs, and directory structures\n"
            "- Report back exactly what you implemented, including file paths and key decisions\n"
            "- Ask the Architect for clarification if instructions are unclear\n\n"
            "COMMUNICATION RULES:\n"
            "- You receive instructions from the Architect agent\n"
            "- Your response will be sent back to the Architect for review\n"
            "- Be specific in your reports: list files created/modified, key functions, any issues found\n"
            "- If you encounter a problem, explain it clearly and suggest solutions\n\n"
            "CODING STANDARDS:\n"
            "- Write actual code files — don't just describe what you'd do\n"
            "- Include error handling and input validation\n"
            "- Use clear naming conventions and add brief comments for complex logic\n"
            "- Follow the language/framework best practices for the project"
        ),
    },
]

# Conversation store
STORE_DIR = Path(os.getenv("CONVERSATION_STORE_DIR", "~/Downloads/telegram-claude-bot/data")).expanduser()
STORE_DIR.mkdir(parents=True, exist_ok=True)

# Telegram message limits
MAX_TELEGRAM_MESSAGE_LENGTH = 4000

# Predefined projects
PREDEFINED_PROJECTS = {
    "family-finance": "~/Downloads/family-finance",
    "skillup": "~/Downloads/SkillUp",
    "telegram-claude-bot": "~/Downloads/telegram-claude-bot",
}
