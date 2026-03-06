from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, constants
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from orchestrator import OrchestratorManager
from sdk_client import ClaudeSDKManager
from session_manager import SessionManager
from config import (
    ALLOWED_USER_IDS,
    DEFAULT_AGENTS,
    MAX_TELEGRAM_MESSAGE_LENGTH,
    PROJECTS_BASE_DIR,
    TELEGRAM_BOT_TOKEN,
    PREDEFINED_PROJECTS,
    MAX_TURNS_PER_CYCLE,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states for /new flow
NAME, DESCRIPTION, DIRECTORY, AGENTS_COUNT = range(4)

# Global state: user_id -> {project_id -> OrchestratorManager}
_state_lock = asyncio.Lock()
active_sessions: dict[int, dict[str, OrchestratorManager]] = {}
current_project: dict[int, str] = {}

# SDK + SessionManager singletons (initialized in post_init)
sdk_client: ClaudeSDKManager | None = None
session_mgr: SessionManager | None = None

# Valid project name pattern
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]+$")


# --- Authorization ---
def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


async def _check_auth(update: Update) -> bool:
    user_id = update.effective_user.id
    if is_authorized(user_id):
        return True
    logger.warning(f"Unauthorized access attempt by user {user_id}")
    if update.message:
        await update.message.reply_text("⛔ Unauthorized. Contact the bot admin.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
    return False


def get_user_sessions(user_id: int) -> dict[str, OrchestratorManager]:
    """Get or create the sessions dict for a user. Caller MUST hold _state_lock."""
    if user_id not in active_sessions:
        active_sessions[user_id] = {}
    return active_sessions[user_id]


def get_current_manager(user_id: int) -> OrchestratorManager | None:
    """Get the current OrchestratorManager for a user. Caller MUST hold _state_lock."""
    sessions = get_user_sessions(user_id)
    proj_id = current_project.get(user_id)
    if proj_id and proj_id in sessions:
        return sessions[proj_id]
    return None


async def send_long_message(bot, chat_id: int, text: str, **kwargs):
    """Send a message, splitting if it exceeds Telegram's limit."""
    if not text or not text.strip():
        return

    async def _send(msg_text):
        msg_text = msg_text.strip()
        if not msg_text:
            return
        try:
            await bot.send_message(chat_id=chat_id, text=msg_text, **kwargs)
        except Exception as e:
            logger.error(f"Failed to send message to chat {chat_id}: {e}")

    if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        await _send(text)
        return

    chunks = []
    while text:
        if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_TELEGRAM_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_TELEGRAM_MESSAGE_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    for chunk in chunks:
        await _send(chunk)
        await asyncio.sleep(0.3)


def _make_on_update(bot, chat_id: int, project_id: str):
    """Create an on_update callback with properly captured references."""
    async def on_update(text: str):
        await send_long_message(bot, chat_id, text)
    return on_update


async def _activate_project(
    user_id: int,
    chat_id: int,
    bot,
    project_id: str,
    project_name: str,
    project_dir: str,
    agents_count: int,
    description: str | None = None,
) -> OrchestratorManager:
    """Create an OrchestratorManager, register it, set current project, save to session_mgr.

    Caller MUST hold _state_lock.
    """
    os.makedirs(project_dir, exist_ok=True)
    on_update = _make_on_update(bot, chat_id, project_id)

    # agents_count >= 2 means multi-agent (orchestrator delegates freely)
    multi_agent = agents_count >= 2

    manager = OrchestratorManager(
        project_name=project_name,
        project_dir=project_dir,
        sdk=sdk_client,
        session_mgr=session_mgr,
        user_id=user_id,
        project_id=project_id,
        on_update=on_update,
        multi_agent=multi_agent,
    )

    sessions = get_user_sessions(user_id)
    sessions[project_id] = manager
    current_project[user_id] = project_id

    # Save project metadata if not already saved
    existing = await session_mgr.load_project(project_id)
    if not existing:
        await session_mgr.save_project(
            project_id=project_id,
            user_id=user_id,
            name=project_name,
            description=description or f"Project: {project_name}",
            project_dir=project_dir,
        )

    return manager


# --- /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    text = (
        "🤖 *Claude Code Bot*\n\n"
        "Build applications with Claude Code agents — "
        "solo or as a multi-agent team.\n\n"
        "*Commands:*\n"
        "/new — Create a new project / פרויקט חדש\n"
        "/projects — List all projects / רשימת פרויקטים\n"
        "/switch <name> — Switch active project\n"
        "/status — Current project status\n"
        "/talk <agent> <msg> — Message a specific agent\n"
        "/pause — Pause current project\n"
        "/resume — Resume current project\n"
        "/stop — Stop current project\n"
        "/log — Recent conversation log\n"
        "/cancel — Cancel current operation\n"
        "/help — Show this help"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# --- /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await start_command(update, context)


# --- /new conversation flow ---
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await update.message.reply_text("📝 *New Project*\n\nWhat's the project name?", parse_mode="Markdown")
    return NAME


async def new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("❌ Project name cannot be empty. Please enter a name:")
        return NAME

    if len(name) > 50:
        await update.message.reply_text("❌ Project name is too long (max 50 characters). Please enter a shorter name:")
        return NAME

    if not _PROJECT_NAME_RE.match(name):
        await update.message.reply_text(
            "❌ Project name contains invalid characters.\n"
            "Only letters, numbers, spaces, hyphens, and underscores are allowed.\n\n"
            "Please enter a valid name:"
        )
        return NAME

    context.user_data["new_project_name"] = name
    await update.message.reply_text(
        f"Project: *{name}*\n\nDescribe the application you want to build:",
        parse_mode="Markdown",
    )
    return DESCRIPTION


async def new_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_project_description"] = update.message.text.strip()
    await update.message.reply_text(
        "📁 *Project directory*\n\n"
        "Enter the full path to the project directory, or type `auto` to create one.\n\n"
        "Examples:\n"
        "• `~/Downloads/family-finance`\n"
        "• `~/Downloads/SkillUp`\n"
        "• `auto` (creates under ~/claude-projects/)",
        parse_mode="Markdown",
    )
    return DIRECTORY


async def new_directory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dir_input = update.message.text.strip()
    if dir_input.lower() == "auto":
        name = context.user_data["new_project_name"]
        safe_name = name.lower().replace(" ", "-")
        project_dir = str(PROJECTS_BASE_DIR / safe_name)
    else:
        project_dir = str(Path(dir_input).expanduser().resolve())

    parent = Path(project_dir).parent
    if not parent.exists():
        await update.message.reply_text(
            f"❌ Parent directory does not exist: `{parent}`\n\n"
            "Please enter a valid path or type `auto`:",
            parse_mode="Markdown",
        )
        return DIRECTORY

    if parent.exists() and not os.access(str(parent), os.W_OK):
        await update.message.reply_text(
            f"❌ No write permission to: `{parent}`\n\n"
            "Please enter a different path or type `auto`:",
            parse_mode="Markdown",
        )
        return DIRECTORY

    context.user_data["new_project_dir"] = project_dir
    await update.message.reply_text(
        f"Directory: `{project_dir}`\n\n"
        "How many agents? (default: 2 — orchestrator + developer)\n\n"
        "Options:\n"
        "• `1` — Solo (orchestrator handles everything directly)\n"
        "• `2` — Orchestrator + Developer\n"
        "• `3` — Orchestrator + Developer + Reviewer\n"
        "• Or type a number",
        parse_mode="Markdown",
    )
    return AGENTS_COUNT


async def new_agents_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        count = int(text)
    except ValueError:
        count = 2

    count = max(1, min(count, 5))

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    name = context.user_data["new_project_name"]
    description = context.user_data["new_project_description"]
    project_dir = context.user_data["new_project_dir"]
    project_id = name.lower().replace(" ", "-")

    bot = context.bot
    async with _state_lock:
        manager = await _activate_project(
            user_id=user_id,
            chat_id=chat_id,
            bot=bot,
            project_id=project_id,
            project_name=name,
            project_dir=project_dir,
            agents_count=count,
            description=description,
        )

    if count == 1:
        agent_desc = "🤖 Agent: *orchestrator* (Solo mode)"
        mode_msg = "Ready to work on your request."
    else:
        agent_names = manager.agent_names[:count]
        agent_desc = f"🤖 Agents: {', '.join(agent_names)}"
        mode_msg = "Starting multi-agent orchestration..."

    await update.message.reply_text(
        f"✅ Project *{name}* created!\n"
        f"📁 Directory: `{project_dir}`\n"
        f"{agent_desc}\n\n"
        f"{mode_msg}",
        parse_mode="Markdown",
    )

    # Start the session
    await manager.start_session(description)

    return ConversationHandler.END


async def new_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# --- /projects ---
async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        sessions = get_user_sessions(user_id)
        saved = await session_mgr.list_projects()

        keyboard = []

        if sessions:
            for pid, mgr in sessions.items():
                state = "running" if mgr.is_running else ("paused" if mgr.is_paused else "stopped")
                keyboard.append([InlineKeyboardButton(f"🟢 {mgr.project_name} [{state}]", callback_data=f"sel_proj:{pid}")])

        if saved:
            active_ids = sessions.keys()
            for p in saved:
                if p['project_id'] not in active_ids:
                    keyboard.append([InlineKeyboardButton(f"💾 {p['name']} (Saved)", callback_data=f"sel_proj:{p['project_id']}")])

        if PREDEFINED_PROJECTS:
            for name in PREDEFINED_PROJECTS.keys():
                if name not in sessions and not any(p['project_id'] == name for p in saved):
                    keyboard.append([InlineKeyboardButton(f"📁 {name} (Predefined)", callback_data=f"sel_proj:{name}")])

    if not keyboard:
        await update.message.reply_text("No projects available. Use /new to create one.")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👇 *Select a project to start/continue:*",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def project_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data.startswith("sel_proj:"):
        proj_id = data.split(":", 1)[1]
        context.user_data['pending_switch'] = proj_id

        keyboard = [
            [
                InlineKeyboardButton("1 (Solo)", callback_data="set_agents:1"),
                InlineKeyboardButton("2 (+ Dev)", callback_data="set_agents:2"),
            ],
            [
                InlineKeyboardButton("3 (+ Reviewer)", callback_data="set_agents:3"),
                InlineKeyboardButton("4 (+ Tester)", callback_data="set_agents:4"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Selected: *{proj_id}*\n\nHow many agents should work on this project?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    elif data.startswith("set_agents:"):
        agents_count = int(data.split(":", 1)[1])
        target = context.user_data.get('pending_switch')

        if not target:
            await query.edit_message_text("Error: no project selected.")
            return

        chat_id = update.effective_chat.id
        bot = context.bot

        async with _state_lock:
            sessions = get_user_sessions(user_id)

            if target in sessions:
                current_project[user_id] = target
                mgr = sessions[target]
                await query.edit_message_text(
                    f"✅ Switched to *{mgr.project_name}* (already active).\n\nSend me a message to continue working.",
                    parse_mode="Markdown"
                )
            elif target in PREDEFINED_PROJECTS:
                project_dir = str(Path(PREDEFINED_PROJECTS[target]).expanduser().resolve())
                await _activate_project(
                    user_id=user_id,
                    chat_id=chat_id,
                    bot=bot,
                    project_id=target,
                    project_name=target,
                    project_dir=project_dir,
                    agents_count=agents_count,
                    description=f"Predefined project: {target}",
                )

                await query.edit_message_text(
                    f"✅ {'Agent' if agents_count == 1 else f'Team of {agents_count} agents'} ready for *{target}*.\n\n"
                    f"Send me a message to start!",
                    parse_mode="Markdown"
                )
            else:
                # Saved project
                saved_list = await session_mgr.list_projects()
                if any(p['project_id'] == target for p in saved_list):
                    data_state = await session_mgr.load_project(target)
                    project_dir = data_state.get("project_dir", str(PROJECTS_BASE_DIR / target))
                    project_name = data_state.get("name", target)

                    await _activate_project(
                        user_id=user_id,
                        chat_id=chat_id,
                        bot=bot,
                        project_id=target,
                        project_name=project_name,
                        project_dir=project_dir,
                        agents_count=agents_count,
                    )

                    await query.edit_message_text(
                        f"✅ Restored *{target}* with {agents_count} agent(s).\n\n"
                        f"Send me a message to continue working.",
                        parse_mode="Markdown"
                    )
                else:
                    await query.edit_message_text(f"Project '{target}' not found.")


# --- /switch ---
async def switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        sessions = get_user_sessions(user_id)

        if not context.args:
            if sessions:
                names = ", ".join(sessions.keys())
                await update.message.reply_text(f"Usage: `/switch <project-name> [num_agents]`\n\nActive projects: {names}", parse_mode="Markdown")
            else:
                await update.message.reply_text("Usage: `/switch <project-name> [num_agents]`\nNo active projects. Use /new to create one.", parse_mode="Markdown")
            return

        target = context.args[0].lower()
        chat_id = update.effective_chat.id
        bot = context.bot

        if target in sessions:
            current_project[user_id] = target
            mgr = sessions[target]
            await update.message.reply_text(f"Switched to *{mgr.project_name}*", parse_mode="Markdown")
        elif target in PREDEFINED_PROJECTS:
            project_dir = str(Path(PREDEFINED_PROJECTS[target]).expanduser().resolve())

            agents_count = 2
            if len(context.args) > 1 and context.args[1].isdigit():
                agents_count = int(context.args[1])

            await _activate_project(
                user_id=user_id,
                chat_id=chat_id,
                bot=bot,
                project_id=target,
                project_name=target,
                project_dir=project_dir,
                agents_count=agents_count,
                description=f"Predefined project: {target}",
            )

            await update.message.reply_text(f"Switched to and initialized predefined project *{target}* in `{project_dir}`.", parse_mode="Markdown")
        else:
            saved = await session_mgr.list_projects()
            saved_ids = [p['project_id'] for p in saved]
            if target in saved_ids:
                data = await session_mgr.load_project(target)
                project_dir = data.get("project_dir", str(PROJECTS_BASE_DIR / target))
                agents_count = 2

                if len(context.args) > 1 and context.args[1].isdigit():
                    agents_count = int(context.args[1])

                await _activate_project(
                    user_id=user_id,
                    chat_id=chat_id,
                    bot=bot,
                    project_id=target,
                    project_name=data.get("name", target),
                    project_dir=project_dir,
                    agents_count=agents_count,
                )

                await update.message.reply_text(f"Restored and switched to saved project *{target}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"Project '{target}' not found. Use /projects to see available.")


# --- /status ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        sessions = get_user_sessions(user_id)

        if not sessions:
            await update.message.reply_text("No active projects. Use /projects to open a room.")
            return

        cur = current_project.get(user_id, "")
        lines = ["📊 *Project Status:*\n"]

        for pid, mgr in sessions.items():
            marker = "👉" if pid == cur else "🚪"
            state = "running" if mgr.is_running else ("paused" if mgr.is_paused else "ready")
            lines.append(f"{marker} *{mgr.project_name}* (`{pid}`)")
            lines.append(f"  State: {state} | Turn: {mgr.turn_count}/{MAX_TURNS_PER_CYCLE}")
            lines.append(f"  Cost: ${mgr.total_cost_usd:.4f} | Agents: {', '.join(mgr.agent_names)}")

            if mgr.conversation_log:
                last = mgr.conversation_log[-1]
                lines.append(f"  Last: {last.agent_name} ({last.role})")
            lines.append("")

        lines.append("Use /switch to change your active project.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# --- /talk ---
async def talk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    if not context.args or len(context.args) < 2:
        agents = ", ".join(manager.agent_names)
        await update.message.reply_text(
            f"Usage: `/talk <agent> <message>`\n\nAvailable agents: {agents}",
            parse_mode="Markdown",
        )
        return

    agent_name = context.args[0].lower()
    message = " ".join(context.args[1:])

    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    await manager.inject_user_message(agent_name, message)


# --- /pause ---
async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)
        proj_id = current_project.get(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    manager.pause()
    await session_mgr.update_status(proj_id, "paused")
    await update.message.reply_text(f"⏸ Project *{manager.project_name}* paused.", parse_mode="Markdown")


# --- /resume ---
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)
        proj_id = current_project.get(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    manager.resume()
    await session_mgr.update_status(proj_id, "active")
    await update.message.reply_text(f"▶️ Project *{manager.project_name}* resumed.", parse_mode="Markdown")


# --- /stop ---
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)

        if not manager:
            await update.message.reply_text("No active project. Use /new or /switch.")
            return

        proj_id = current_project[user_id]

    await manager.stop()
    await session_mgr.update_status(proj_id, "stopped")

    async with _state_lock:
        sessions = get_user_sessions(user_id)
        sessions.pop(proj_id, None)
        if proj_id == current_project.get(user_id):
            if sessions:
                current_project[user_id] = next(iter(sessions))
            else:
                current_project.pop(user_id, None)


# --- /log ---
async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        proj_id = current_project.get(user_id)
        manager = get_current_manager(user_id)

    if not manager or not proj_id:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    # Load from persistent storage (survives restarts)
    messages = await session_mgr.get_recent_messages(proj_id, count=15)

    # Fall back to in-memory log if DB is empty
    if not messages and manager.conversation_log:
        messages = [
            {
                "agent_name": m.agent_name,
                "role": m.role,
                "content": m.content,
                "cost_usd": m.cost_usd,
                "timestamp": m.timestamp,
            }
            for m in manager.conversation_log[-15:]
        ]

    if not messages:
        state = "running" if manager.is_running else ("paused" if manager.is_paused else "ready")
        await update.message.reply_text(
            f"📜 *{manager.project_name}* — No messages yet.\n\n"
            f"State: {state}\n"
            f"Agents: {', '.join(manager.agent_names)}\n\n"
            f"Send a text message to start a conversation.",
            parse_mode="Markdown"
        )
        return

    lines = [f"📜 *Last {len(messages)} messages — {manager.project_name}:*\n"]
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else msg.content
        agent_name = msg.get("agent_name", "") if isinstance(msg, dict) else msg.agent_name
        role = msg.get("role", "") if isinstance(msg, dict) else msg.role
        cost = msg.get("cost_usd", 0) if isinstance(msg, dict) else msg.cost_usd

        content_preview = content[:400]
        if len(content) > 400:
            content_preview += "..."

        cost_info = f" (${cost:.4f})" if cost > 0 else ""
        lines.append(f"*{agent_name}* ({role}){cost_info}:\n{content_preview}\n")

    text = "\n".join(lines)
    await send_long_message(context.bot, update.effective_chat.id, text, parse_mode="Markdown")


# --- normal text messages ---
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text(
            "No active project selected.\n\n"
            "Use /projects to see available projects, or /new to create one.",
        )
        return

    message = update.message.text.strip()

    # Show typing indicator
    await update.effective_chat.send_action(constants.ChatAction.TYPING)

    if not manager.is_running:
        await manager.start_session(message)
    else:
        # Running — inject message to orchestrator
        await manager.inject_user_message("orchestrator", message)


# --- Error handler ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}", exc_info=context.error)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ An error occurred: {context.error}",
            )
        except Exception:
            pass


def main():
    global sdk_client, session_mgr

    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("ERROR: Set TELEGRAM_BOT_TOKEN in .env file")
        return

    # Initialize SDK client
    sdk_client = ClaudeSDKManager()

    async def post_init(application: Application):
        global session_mgr

        # Initialize session manager (async — needs event loop)
        session_mgr = SessionManager()
        await session_mgr.initialize()

        commands = [
            BotCommand("projects", "List available projects"),
            BotCommand("status", "View status of all active rooms"),
            BotCommand("switch", "Switch your active project room"),
            BotCommand("log", "Read the conversation log"),
            BotCommand("talk", "Talk to a specific agent directly"),
            BotCommand("pause", "Pause the current project"),
            BotCommand("resume", "Resume the current project"),
            BotCommand("stop", "Stop the current project"),
            BotCommand("new", "Create a completely new project"),
            BotCommand("help", "Show help"),
        ]
        await application.bot.set_my_commands(commands)

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .concurrent_updates(True)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Conversation handler for /new
    new_handler = ConversationHandler(
        entry_points=[CommandHandler("new", new_command)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_name)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_description)],
            DIRECTORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_directory)],
            AGENTS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_agents_count)],
        },
        fallbacks=[CommandHandler("cancel", new_cancel)],
    )

    app.add_handler(new_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("projects", projects_command))
    app.add_handler(CommandHandler("switch", switch_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("talk", talk_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("log", log_command))

    # Callback query handler
    app.add_handler(CallbackQueryHandler(project_callback_handler))

    # Catch-all text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    app.add_error_handler(error_handler)

    logger.info("Bot starting with SDK + Orchestrator architecture...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
