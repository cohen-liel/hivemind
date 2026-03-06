from __future__ import annotations

import asyncio
import logging
import os
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

from agent_manager import AgentManager
from config import (
    DEFAULT_AGENTS,
    MAX_TELEGRAM_MESSAGE_LENGTH,
    PROJECTS_BASE_DIR,
    TELEGRAM_BOT_TOKEN,
    PREDEFINED_PROJECTS,
    MAX_TURNS_PER_CYCLE,
)
from conversation_store import ConversationStore

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states for /new flow
NAME, DESCRIPTION, DIRECTORY, AGENTS_COUNT = range(4)

# Global state: user_id -> {project_id -> AgentManager}
active_sessions: dict[int, dict[str, AgentManager]] = {}
# Track which project each user is currently focused on
current_project: dict[int, str] = {}

store = ConversationStore()


def get_user_sessions(user_id: int) -> dict[str, AgentManager]:
    if user_id not in active_sessions:
        active_sessions[user_id] = {}
    return active_sessions[user_id]


def get_current_manager(user_id: int) -> AgentManager | None:
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
            if hasattr(bot, "send_message"):
                await bot.send_message(chat_id=chat_id, text=msg_text, **kwargs)
            elif hasattr(bot, "bot"):
                await bot.bot.send_message(chat_id=chat_id, text=msg_text, **kwargs)
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
        for line in text.split("\n"):
            if line.strip():
                store.add_message(project_id, "system", "update", text)
                break
    return on_update


# --- /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Claude Code Multi-Agent Bot*\n\n"
        "This bot lets you create multi-agent Claude Code sessions "
        "that collaborate to build applications.\n\n"
        "You can run *multiple projects in parallel* — each with its own agents.\n\n"
        "*Commands:*\n"
        "/new — Create a new project\n"
        "/projects — List all projects\n"
        "/switch <name> — Switch active project\n"
        "/status — Current project status\n"
        "/talk <agent> <msg> — Message a specific agent\n"
        "/pause — Pause current project\n"
        "/resume — Resume current project\n"
        "/stop — Stop current project\n"
        "/log — Recent conversation log\n"
        "/help — Show this help"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# --- /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


# --- /new conversation flow ---
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 *New Project*\n\nWhat's the project name?", parse_mode="Markdown")
    return NAME


async def new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
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

    context.user_data["new_project_dir"] = project_dir
    await update.message.reply_text(
        f"Directory: `{project_dir}`\n\n"
        "How many agents? (default: 2 — architect + developer)\n\n"
        "Options:\n"
        "• `2` — Architect + Developer\n"
        "• `3` — Architect + Developer + Reviewer\n"
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

    # Build agents config
    agents_config = _build_agents_config(count)

    os.makedirs(project_dir, exist_ok=True)

    # Capture bot reference for the closure — NOT the context object
    bot = context.bot
    on_update = _make_on_update(bot, chat_id, project_id)

    manager = AgentManager(
        project_name=name,
        project_dir=project_dir,
        agents_config=agents_config,
        on_update=on_update,
    )

    sessions = get_user_sessions(user_id)
    sessions[project_id] = manager
    current_project[user_id] = project_id

    # Save project metadata
    store.save_project(project_id, {
        "project_id": project_id,
        "name": name,
        "description": description,
        "project_dir": project_dir,
        "agents": [a["name"] for a in agents_config],
        "status": "active",
        "user_id": user_id,
        "messages": [],
        "created_at": time.time(),
    })

    await update.message.reply_text(
        f"✅ Project *{name}* created!\n"
        f"📁 Directory: `{project_dir}`\n"
        f"🤖 Agents: {', '.join(a['name'] for a in agents_config)}\n\n"
        f"Starting agent conversation...",
        parse_mode="Markdown",
    )

    # Start the session
    await manager.start_session(description)

    return ConversationHandler.END


async def new_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def _build_agents_config(count: int) -> list[dict]:
    reviewer_prompt = (
        "You are the **Reviewer** agent in a multi-agent coding team.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "- Review code written by the Developer for bugs, security issues, and best practices\n"
        "- Suggest improvements and optimizations\n"
        "- Verify the implementation matches the Architect's plan\n"
        "- When everything looks good, respond with TASK_COMPLETE\n\n"
        "Be thorough but constructive in your reviews."
    )
    all_roles = DEFAULT_AGENTS + [
        {"name": "reviewer", "role": "Reviewer", "system_prompt": reviewer_prompt},
        {"name": "tester", "role": "Tester", "system_prompt": (
            "You are the **Tester** agent. Write and run tests for the code. "
            "Report test results and any failures to the team."
        )},
        {"name": "devops", "role": "DevOps", "system_prompt": (
            "You are the **DevOps** agent. Handle deployment configs, CI/CD, "
            "Docker, and infrastructure setup."
        )},
    ]
    return all_roles[:count]


# --- /projects ---
async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = get_user_sessions(user_id)
    saved = store.list_projects()

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
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data.startswith("sel_proj:"):
        proj_id = data.split(":", 1)[1]
        context.user_data['pending_switch'] = proj_id

        # Ask for number of agents
        keyboard = [
            [
                InlineKeyboardButton("1 (Main only)", callback_data="set_agents:1"),
                InlineKeyboardButton("2 (Main + Dev)", callback_data="set_agents:2"),
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

        sessions = get_user_sessions(user_id)
        chat_id = update.effective_chat.id
        # Capture bot reference — this persists and is always valid
        bot = context.bot

        if target in sessions:
            current_project[user_id] = target
            mgr = sessions[target]
            await query.edit_message_text(
                f"✅ Switched to *{mgr.project_name}* (already active).\n\nSend me a message to continue working.",
                parse_mode="Markdown"
            )
        elif target in PREDEFINED_PROJECTS:
            project_dir = str(Path(PREDEFINED_PROJECTS[target]).expanduser().resolve())
            agents_config = _build_agents_config(agents_count)
            os.makedirs(project_dir, exist_ok=True)

            on_update = _make_on_update(bot, chat_id, target)

            manager = AgentManager(
                project_name=target,
                project_dir=project_dir,
                agents_config=agents_config,
                on_update=on_update,
            )
            sessions[target] = manager
            current_project[user_id] = target

            if not store.load_project(target):
                store.save_project(target, {
                    "project_id": target,
                    "name": target,
                    "description": f"Predefined project: {target}",
                    "project_dir": project_dir,
                    "agents": [a["name"] for a in agents_config],
                    "status": "active",
                    "user_id": user_id,
                    "messages": [],
                    "created_at": time.time(),
                })

            await query.edit_message_text(
                f"✅ Team of {agents_count} agent(s) ready for *{target}*.\n\n"
                f"Send me a message to start giving them tasks!",
                parse_mode="Markdown"
            )
        else:
            # Saved project
            saved_list = store.list_projects()
            if any(p['project_id'] == target for p in saved_list):
                data_state = store.load_project(target)
                project_dir = data_state.get("project_dir", str(PROJECTS_BASE_DIR / target))
                agents_config = _build_agents_config(agents_count)

                on_update = _make_on_update(bot, chat_id, target)

                manager = AgentManager(
                    project_name=data_state.get("name", target),
                    project_dir=project_dir,
                    agents_config=agents_config,
                    on_update=on_update,
                )
                sessions[target] = manager
                current_project[user_id] = target

                await query.edit_message_text(
                    f"✅ Restored *{target}* with {agents_count} agent(s).\n\n"
                    f"Send me a message to continue working.",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(f"Project '{target}' not found.")


# --- /switch ---
async def switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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

        agents_config = _build_agents_config(agents_count)
        os.makedirs(project_dir, exist_ok=True)

        on_update = _make_on_update(bot, chat_id, target)

        manager = AgentManager(
            project_name=target,
            project_dir=project_dir,
            agents_config=agents_config,
            on_update=on_update,
        )
        sessions[target] = manager
        current_project[user_id] = target

        if not store.load_project(target):
            store.save_project(target, {
                "project_id": target,
                "name": target,
                "description": f"Predefined project: {target}",
                "project_dir": project_dir,
                "agents": [a["name"] for a in agents_config],
                "status": "active",
                "user_id": user_id,
                "messages": [],
                "created_at": time.time(),
            })

        await update.message.reply_text(f"Switched to and initialized predefined project *{target}* in `{project_dir}`.", parse_mode="Markdown")
    else:
        saved = [p['project_id'] for p in store.list_projects()]
        if target in saved:
            data = store.load_project(target)
            project_dir = data.get("project_dir", str(PROJECTS_BASE_DIR / target))
            agents_config = _build_agents_config(len(data.get("agents", [1, 2])))

            on_update = _make_on_update(bot, chat_id, target)

            manager = AgentManager(
                project_name=data.get("name", target),
                project_dir=project_dir,
                agents_config=agents_config,
                on_update=on_update,
            )
            sessions[target] = manager
            current_project[user_id] = target

            await update.message.reply_text(f"Restored and switched to saved project *{target}*.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Project '{target}' not found. Use /projects to see available.")


# --- /status ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
    user_id = update.effective_user.id
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

    # Show typing while agent processes
    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    await manager.inject_user_message(agent_name, message)


# --- /pause ---
async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    manager.pause()
    store.update_status(current_project[user_id], "paused")
    await update.message.reply_text(f"⏸ Project *{manager.project_name}* paused.", parse_mode="Markdown")


# --- /resume ---
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    manager.resume()
    store.update_status(current_project[user_id], "active")
    await update.message.reply_text(f"▶️ Project *{manager.project_name}* resumed.", parse_mode="Markdown")


# --- /stop ---
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    proj_id = current_project[user_id]
    await manager.stop()
    store.update_status(proj_id, "stopped")

    sessions = get_user_sessions(user_id)
    sessions.pop(proj_id, None)
    if proj_id == current_project.get(user_id):
        if sessions:
            current_project[user_id] = next(iter(sessions))
        else:
            current_project.pop(user_id, None)


# --- /log ---
async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    proj_id = current_project.get(user_id)
    manager = get_current_manager(user_id)

    if not manager or not proj_id:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    messages = manager.conversation_log[-15:]
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
        content_preview = msg.content[:400]
        if len(msg.content) > 400:
            content_preview += "..."

        cost_info = f" (${msg.cost_usd:.4f})" if msg.cost_usd > 0 else ""
        lines.append(f"*{msg.agent_name}* ({msg.role}){cost_info}:\n{content_preview}\n")

    text = "\n".join(lines)
    await send_long_message(context.bot, update.effective_chat.id, text, parse_mode="Markdown")


# --- normal text messages ---
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    manager = get_current_manager(user_id)

    # 1. No active project
    if not manager:
        await update.message.reply_text(
            "No active project selected.\n\n"
            "Use /projects to see available projects, or /new to create one.",
        )
        return

    message = update.message.text.strip()

    # Show typing indicator
    await update.effective_chat.send_action(constants.ChatAction.TYPING)

    # 2. Single agent mode — always direct send, no loop
    if not manager.is_multi_agent:
        if not manager.is_running:
            await manager.inject_user_message(manager.agent_names[0], message)
        else:
            # Agent is still processing previous message — queue it
            await update.message.reply_text(
                f"⏳ *{manager.agent_names[0]}* is still processing the previous message. "
                f"Please wait for the response.",
                parse_mode="Markdown"
            )
        return

    # 3. Multi-agent mode
    if not manager.is_running and not manager.is_paused:
        await update.message.reply_text(
            f"🚀 Starting *{manager.project_name}* agents...",
            parse_mode="Markdown"
        )
        await manager.start_session(message)
        return

    # 4. Multi-agent running — inject into conversation
    default_agent = manager.agent_names[0]
    await manager.inject_user_message(default_agent, message)


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
    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("ERROR: Set TELEGRAM_BOT_TOKEN in .env file")
        return

    async def post_init(application: Application):
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

    logger.info("Bot starting with concurrent updates enabled...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
