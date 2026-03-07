from __future__ import annotations

import asyncio
import functools
import html
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, constants
from telegram.ext import (
    Application,
    CommandHandler,
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
    MAX_USER_MESSAGE_LENGTH,
    PROJECTS_BASE_DIR,
    TELEGRAM_BOT_TOKEN,
    PREDEFINED_PROJECTS,
    MAX_TURNS_PER_CYCLE,
    RATE_LIMIT_SECONDS,
)
import state

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Aliases to shared state (for backward compat within this module)
_state_lock = state._state_lock
active_sessions = state.active_sessions
current_project = state.current_project
_PROJECT_NAME_RE = state.PROJECT_NAME_RE
_user_last_message = state.user_last_message
_msg_to_project = state.msg_to_project

# --- Constants ---
HANDLER_TIMEOUT_SECONDS = 120  # Max time for any handler before timeout
API_CALL_TIMEOUT_SECONDS = 30  # Timeout for individual Claude API calls injected via /talk
RATE_LIMIT_CLEANUP_INTERVAL = 300  # Clean up stale rate-limit entries every 5 minutes
RATE_LIMIT_ENTRY_TTL = 600  # Remove rate-limit entries older than 10 minutes
_BOT_START_TIME = time.monotonic()  # Track uptime for /health

# Regex for sanitizing user input — allows letters, numbers, common punctuation, whitespace
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # Strip control chars except \n \r \t

# Allowed agent names (whitelist for /talk)
_VALID_AGENT_NAMES = frozenset({"orchestrator", "developer", "reviewer", "tester", "devops"})

# Track last rate-limit cleanup time
_last_rate_limit_cleanup: float = 0.0


# ============================================================
# Error Handling Decorator
# ============================================================

def handler_guard(func: Callable) -> Callable:
    """Decorator that wraps Telegram handler functions with comprehensive error handling.

    - Catches all exceptions and logs them with context
    - Sends a user-friendly error message (never raw tracebacks)
    - Prevents one handler crash from taking down the bot
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        handler_name = func.__name__
        try:
            return await func(update, context, *args, **kwargs)
        except asyncio.CancelledError:
            # Let cancellations propagate — don't swallow them
            raise
        except asyncio.TimeoutError:
            logger.error(f"[{handler_name}] Timed out", exc_info=True)
            await _safe_reply(update, context, "⏱ Operation timed out. Please try again.")
        except PermissionError as e:
            logger.error(f"[{handler_name}] Permission error: {e}")
            await _safe_reply(update, context, f"🚫 Permission denied: {e}")
        except Exception as e:
            logger.error(f"[{handler_name}] Unhandled exception: {e}", exc_info=True)
            await _safe_reply(
                update, context,
                "❌ Something went wrong. The error has been logged.\n"
                "Please try again or use /help."
            )
    return wrapper


async def _safe_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a reply to the user, handling all possible failure modes."""
    try:
        if update and update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
            try:
                await update.callback_query.edit_message_text(text)
                return
            except Exception:
                pass

        if update and update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")


# ============================================================
# Input Validation & Sanitization
# ============================================================

def _sanitize_text(text: str | None) -> str:
    """Sanitize user input text by removing control characters and trimming."""
    if not text:
        return ""
    # Strip control characters (keep newlines, carriage returns, tabs)
    cleaned = _SAFE_TEXT_RE.sub("", text)
    # Collapse excessive whitespace (more than 5 consecutive newlines)
    cleaned = re.sub(r"\n{6,}", "\n\n\n\n\n", cleaned)
    return cleaned.strip()


def _validate_project_id(project_id: str) -> str | None:
    """Validate and sanitize a project ID. Returns cleaned ID or None if invalid."""
    if not project_id or not isinstance(project_id, str):
        return None
    cleaned = project_id.strip().lower()
    if not cleaned or len(cleaned) > 100:
        return None
    if not _PROJECT_NAME_RE.match(cleaned):
        return None
    return cleaned


def _validate_agent_name(name: str) -> str | None:
    """Validate an agent name against the known whitelist. Returns cleaned name or None."""
    if not name or not isinstance(name, str):
        return None
    cleaned = name.strip().lower()
    if cleaned in _VALID_AGENT_NAMES:
        return cleaned
    return None


def _validate_agents_count(count_str: str) -> int | None:
    """Validate agents count string. Returns int 1-5 or None."""
    try:
        count = int(count_str)
        if 1 <= count <= 5:
            return count
    except (ValueError, TypeError):
        pass
    return None


# ============================================================
# Rate Limiter (improved)
# ============================================================

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


async def _check_rate_limit(update: Update) -> bool:
    """Return False (and warn user) if the user is sending messages too fast.

    Improved: properly handles per-user tracking with periodic cleanup
    of stale entries to prevent memory leaks.
    """
    if RATE_LIMIT_SECONDS <= 0:
        return True

    user_id = update.effective_user.id
    now = time.monotonic()

    # Periodic cleanup of stale entries (every RATE_LIMIT_CLEANUP_INTERVAL seconds)
    global _last_rate_limit_cleanup
    if (now - _last_rate_limit_cleanup) > RATE_LIMIT_CLEANUP_INTERVAL:
        _last_rate_limit_cleanup = now
        stale_cutoff = now - RATE_LIMIT_ENTRY_TTL
        stale_keys = [uid for uid, ts in _user_last_message.items() if ts < stale_cutoff]
        for uid in stale_keys:
            _user_last_message.pop(uid, None)
        if stale_keys:
            logger.debug(f"Rate limiter cleanup: removed {len(stale_keys)} stale entries")

    last = _user_last_message.get(user_id, 0.0)
    elapsed = now - last
    if elapsed < RATE_LIMIT_SECONDS:
        remaining = RATE_LIMIT_SECONDS - elapsed
        if update.message:
            await update.message.reply_text(
                f"⏳ Slow down! Please wait {remaining:.1f}s before sending another message."
            )
        return False

    _user_last_message[user_id] = now
    return True


# ============================================================
# Session / Manager Helpers
# ============================================================

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


# ============================================================
# Telegram Message Helpers
# ============================================================

async def send_long_message(bot, chat_id: int, text: str, project_id: str | None = None, **kwargs) -> list[int]:
    """Send a message, splitting if it exceeds Telegram's limit. Returns sent message IDs."""
    sent_ids: list[int] = []
    if not text or not text.strip():
        return sent_ids

    async def _send(msg_text) -> int | None:
        msg_text = msg_text.strip()
        if not msg_text:
            return None
        try:
            msg = await bot.send_message(chat_id=chat_id, text=msg_text, **kwargs)
            return msg.message_id
        except Exception as e:
            logger.error(f"Failed to send message to chat {chat_id}: {e}")
            return None

    if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        mid = await _send(text)
        if mid:
            sent_ids.append(mid)
            if project_id:
                _msg_to_project[mid] = project_id
        return sent_ids

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
        mid = await _send(chunk)
        if mid:
            sent_ids.append(mid)
            if project_id:
                _msg_to_project[mid] = project_id
        await asyncio.sleep(0.3)

    return sent_ids


# ============================================================
# Progress Message (streaming edits with reactions)
# ============================================================

class ProgressMessage:
    """Manages a single Telegram progress message that gets edited in-place.

    Features (inspired by OpenClaw):
    - Throttled edits (min 1.5s between edits)
    - Initial debounce: waits for 30+ chars before first edit (better push notifications)
    - Regressive update prevention: skips edits where text got shorter (prevents flicker)
    - Status reactions: emoji on user's original message shows agent state
    - Stall detection: reaction changes if agent is silent too long
    """

    THROTTLE_SECONDS = 1.5
    MIN_INITIAL_CHARS = 30
    STALL_SOFT_SECONDS = 15  # 🥱 if silent this long
    STALL_HARD_SECONDS = 45  # 😰 if silent this long

    # Status reaction emojis
    REACTION_QUEUED = "👀"
    REACTION_THINKING = "🤔"
    REACTION_CODING = "👨‍💻"
    REACTION_DONE = "👍"
    REACTION_ERROR = "😱"
    REACTION_STALL_SOFT = "🥱"
    REACTION_STALL_HARD = "😰"

    def __init__(self, bot, chat_id: int, user_message_id: int | None = None, project_id: str | None = None):
        self._bot = bot
        self._chat_id = chat_id
        self._user_message_id = user_message_id  # The user's message to react on
        self._project_id = project_id
        self._progress_msg = None
        self._last_edit_time: float = 0
        self._last_update_time: float = 0  # For stall detection
        self._heartbeat_task: asyncio.Task | None = None
        self._current_text: str = ""
        self._last_sent_text: str = ""  # For regressive update prevention
        self._current_reaction: str = ""
        self._intermediate_ids: list[int] = []  # Track intermediate messages for deletion

    async def start(self):
        """Send initial progress message, set queued reaction, start heartbeat."""
        await self._set_reaction(self.REACTION_QUEUED)
        try:
            self._progress_msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text="🤖 Working...",
            )
        except Exception as e:
            logger.error(f"Failed to send progress message: {e}")
        self._last_update_time = time.monotonic()
        self._heartbeat_task = asyncio.create_task(self._typing_heartbeat())

    async def _set_reaction(self, emoji: str):
        """Set an emoji reaction on the user's original message."""
        if not self._user_message_id or emoji == self._current_reaction:
            return
        try:
            from telegram import ReactionTypeEmoji
            await self._bot.set_message_reaction(
                chat_id=self._chat_id,
                message_id=self._user_message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
            self._current_reaction = emoji
        except Exception as e:
            # Reactions may not be supported in all chat types
            if "not modified" not in str(e).lower():
                logger.debug(f"Could not set reaction: {e}")

    async def _typing_heartbeat(self):
        """Send typing indicator + stall detection."""
        try:
            while True:
                try:
                    await self._bot.send_chat_action(
                        chat_id=self._chat_id,
                        action=constants.ChatAction.TYPING,
                    )
                except Exception:
                    pass

                # Stall detection
                elapsed = time.monotonic() - self._last_update_time
                if elapsed > self.STALL_HARD_SECONDS:
                    await self._set_reaction(self.REACTION_STALL_HARD)
                elif elapsed > self.STALL_SOFT_SECONDS:
                    await self._set_reaction(self.REACTION_STALL_SOFT)

                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    async def update(self, text: str):
        """Update the progress message with throttling + smart filtering."""
        now = time.monotonic()
        self._current_text = text
        self._last_update_time = now

        # Set "thinking" reaction on first meaningful update
        if self._current_reaction == self.REACTION_QUEUED:
            # Detect tool use for specific reaction
            if any(k in text for k in ("💻", "Running:", "Bash")):
                await self._set_reaction(self.REACTION_CODING)
            else:
                await self._set_reaction(self.REACTION_THINKING)
        elif any(k in text for k in ("💻", "Running:", "Bash")):
            await self._set_reaction(self.REACTION_CODING)

        if not self._progress_msg:
            await send_long_message(self._bot, self._chat_id, text)
            return

        # Initial debounce: wait for meaningful content before first edit
        if not self._last_sent_text and len(text) < self.MIN_INITIAL_CHARS:
            return

        # Throttle
        if (now - self._last_edit_time) < self.THROTTLE_SECONDS:
            return

        # Regressive update prevention: skip if new text is shorter substring
        if (self._last_sent_text and
                self._last_sent_text.startswith(text) and
                len(text) < len(self._last_sent_text)):
            return

        display = text[:4000]
        if len(text) > 4000:
            display += "\n... (truncated)"

        try:
            await self._progress_msg.edit_text(display)
            self._last_edit_time = now
            self._last_sent_text = text
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.error(f"Failed to edit progress message: {e}")

    async def send_intermediate(self, text: str):
        """Send an intermediate message, track its ID for later deletion."""
        if not text or not text.strip():
            return
        sent_ids = await send_long_message(
            self._bot, self._chat_id, text, project_id=self._project_id
        )
        self._intermediate_ids.extend(sent_ids)

    async def finish(self, final_text: str):
        """Delete ALL intermediates + progress message, send ONE final clean message."""
        self._stop_heartbeat()
        await self._set_reaction(self.REACTION_DONE)

        # Delete all intermediate messages
        for mid in self._intermediate_ids:
            try:
                await self._bot.delete_message(chat_id=self._chat_id, message_id=mid)
            except Exception:
                pass
        self._intermediate_ids.clear()

        # Delete progress message
        if self._progress_msg:
            try:
                await self._progress_msg.delete()
            except Exception:
                pass
            self._progress_msg = None

        # Send clean final message
        if final_text and final_text.strip():
            await send_long_message(
                self._bot, self._chat_id, final_text, project_id=self._project_id
            )

    async def finish_error(self, error_text: str):
        """Send error response with error reaction."""
        self._stop_heartbeat()
        await self._set_reaction(self.REACTION_ERROR)

        # Delete all intermediate messages
        for mid in self._intermediate_ids:
            try:
                await self._bot.delete_message(chat_id=self._chat_id, message_id=mid)
            except Exception:
                pass
        self._intermediate_ids.clear()

        if self._progress_msg:
            try:
                await self._progress_msg.delete()
            except Exception:
                pass
            self._progress_msg = None

        if error_text and error_text.strip():
            await send_long_message(self._bot, self._chat_id, error_text)

    def _stop_heartbeat(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None


# ============================================================
# Callback factories (bridge Telegram <-> EventBus)
# ============================================================

def _make_callbacks(bot, chat_id: int, user_message_id: int | None = None,
                    project_id: str | None = None, project_name: str | None = None):
    """Create on_update (progress), on_result (intermediate), and on_final callbacks.

    on_update: edits a single progress message in-place with throttling.
    on_result: sends an intermediate message (tracked for later deletion).
    on_final: deletes all intermediates + progress, sends one clean final message.

    All callbacks also broadcast to the dashboard EventBus.
    """
    from dashboard.events import event_bus

    progress = ProgressMessage(bot, chat_id, user_message_id=user_message_id, project_id=project_id)
    started = False
    tag = f"[{project_name}] " if project_name else ""

    async def on_update(text: str):
        nonlocal started
        if not started:
            await progress.start()
            started = True
        await progress.update(f"{tag}{text}")
        await event_bus.publish({
            "type": "agent_update",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
        })

    async def on_result(text: str):
        nonlocal started
        if not started:
            await progress.start()
            started = True
        await progress.send_intermediate(f"{tag}{text}")
        await event_bus.publish({
            "type": "agent_result",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
        })

    async def on_final(text: str):
        nonlocal started
        if started:
            await progress.finish(text)
            started = False
        else:
            await send_long_message(bot, chat_id, text, project_id=project_id)
        await event_bus.publish({
            "type": "agent_final",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
        })

    return on_update, on_result, on_final


# ============================================================
# Project activation
# ============================================================

async def _activate_project(
    user_id: int,
    chat_id: int,
    bot,
    project_id: str,
    project_name: str,
    project_dir: str,
    agents_count: int,
    description: str | None = None,
    user_message_id: int | None = None,
) -> OrchestratorManager:
    """Create an OrchestratorManager, register it, set current project, save to session_mgr.

    Caller MUST hold _state_lock.
    """
    try:
        os.makedirs(project_dir, exist_ok=True)
    except OSError:
        pass  # Directory may exist with macOS restricted permissions

    # Verify the directory is actually accessible (catches macOS sandbox issues)
    try:
        Path(project_dir).stat()
    except PermissionError:
        raise PermissionError(
            f"Cannot access project directory: {project_dir}\n\n"
            f"This usually means the bot is running inside Claude Code's macOS sandbox. "
            f"Start the bot from a normal Terminal instead:\n"
            f"  cd ~/Downloads/telegram-claude-bot && source venv/bin/activate && python bot.py"
        )

    on_update, on_result, on_final = _make_callbacks(
        bot, chat_id, user_message_id=user_message_id,
        project_id=project_id, project_name=project_name,
    )

    # agents_count >= 2 means multi-agent (orchestrator delegates freely)
    multi_agent = agents_count >= 2

    manager = OrchestratorManager(
        project_name=project_name,
        project_dir=project_dir,
        sdk=state.sdk_client,
        session_mgr=state.session_mgr,
        user_id=user_id,
        project_id=project_id,
        on_update=on_update,
        on_result=on_result,
        on_final=on_final,
        multi_agent=multi_agent,
    )

    sessions = get_user_sessions(user_id)
    sessions[project_id] = manager
    current_project[user_id] = project_id

    # Save project metadata if not already saved
    existing = await state.session_mgr.load_project(project_id)
    if not existing:
        await state.session_mgr.save_project(
            project_id=project_id,
            user_id=user_id,
            name=project_name,
            description=description or f"Project: {project_name}",
            project_dir=project_dir,
        )

    # Clean stale messages from old architecture / previous errors
    await state.session_mgr.clear_stale_messages(project_id)

    return manager


# ============================================================
# /start
# ============================================================

@handler_guard
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    text = (
        "🤖 *Claude Code Bot*\n\n"
        "Multi-agent AI team for your projects.\n\n"
        "*Commands:*\n"
        "/projects — Select a project\n"
        "/status — Current project status\n"
        "/health — Bot health check\n"
        "/talk <agent> <msg> — Message a specific agent\n"
        "/log — Conversation log\n"
        "/pause — Pause agents\n"
        "/resume — Resume agents\n"
        "/stop — Stop project\n"
        "/clear — Clear history\n"
        "/help — Show this help"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ============================================================
# /help
# ============================================================

@handler_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    await start_command(update, context)


# ============================================================
# /health — New health check command
# ============================================================

@handler_guard
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return bot health status including uptime, active sessions, memory info."""
    if not await _check_auth(update):
        return

    uptime_seconds = time.monotonic() - _BOT_START_TIME
    uptime_hours = int(uptime_seconds // 3600)
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    uptime_secs = int(uptime_seconds % 60)

    if uptime_hours > 0:
        uptime_str = f"{uptime_hours}h {uptime_minutes}m {uptime_secs}s"
    elif uptime_minutes > 0:
        uptime_str = f"{uptime_minutes}m {uptime_secs}s"
    else:
        uptime_str = f"{uptime_secs}s"

    # Count active sessions across all users
    total_sessions = 0
    running_sessions = 0
    paused_sessions = 0
    total_cost = 0.0

    async with _state_lock:
        for user_id, sessions in active_sessions.items():
            for pid, mgr in sessions.items():
                total_sessions += 1
                if mgr.is_running:
                    running_sessions += 1
                elif mgr.is_paused:
                    paused_sessions += 1
                total_cost += mgr.total_cost_usd

    # SDK client status
    sdk_status = "✅ Connected" if state.sdk_client else "❌ Not initialized"

    # Session manager status
    db_status = "✅ Connected" if state.session_mgr else "❌ Not initialized"

    # Rate limiter stats
    rate_limit_entries = len(_user_last_message)

    lines = [
        "🏥 *Bot Health Status*\n",
        f"⏱ *Uptime:* {uptime_str}",
        f"🔌 *SDK:* {sdk_status}",
        f"💾 *Database:* {db_status}",
        "",
        f"📊 *Sessions:* {total_sessions} total",
        f"  ▶️ Running: {running_sessions}",
        f"  ⏸ Paused: {paused_sessions}",
        f"  ⏹ Idle: {total_sessions - running_sessions - paused_sessions}",
        "",
        f"💰 *Total Cost:* ${total_cost:.4f}",
        f"🚦 *Rate Limiter:* {rate_limit_entries} tracked user(s)",
        f"⚙️ *Rate Limit:* {RATE_LIMIT_SECONDS}s between messages",
        f"🔄 *Max Turns:* {MAX_TURNS_PER_CYCLE}",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# /projects
# ============================================================

@handler_guard
async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        sessions = get_user_sessions(user_id)
        saved = await state.session_mgr.list_projects()

        keyboard = []

        if sessions:
            for pid, mgr in sessions.items():
                mgr_state = "running" if mgr.is_running else ("paused" if mgr.is_paused else "stopped")
                keyboard.append([InlineKeyboardButton(f"🟢 {mgr.project_name} [{mgr_state}]", callback_data=f"sel_proj:{pid}")])

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


# ============================================================
# Callback query handler (inline keyboard presses)
# ============================================================

@handler_guard
async def project_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not isinstance(data, str):
        logger.warning("Received callback query with no data")
        return

    # Handle /talk agent selection
    if data.startswith("talk_agent:"):
        agent_name = _validate_agent_name(data.split(":", 1)[1])
        if not agent_name:
            await query.edit_message_text("❌ Invalid agent name.")
            return

        stored_msg = context.user_data.get('talk_msg')

        if not stored_msg:
            await query.edit_message_text(
                f"Selected *{agent_name}*. Now send your message as a reply to this message, "
                f"or use `/talk {agent_name} <message>`.",
                parse_mode="Markdown",
            )
            context.user_data['talk_target_agent'] = agent_name
            return

        user_id = update.effective_user.id
        async with _state_lock:
            manager = get_current_manager(user_id)

        if not manager:
            await query.edit_message_text("No active project.")
            return

        await query.edit_message_text(f"📨 Sending to *{agent_name}*...", parse_mode="Markdown")

        # Wrap the injection with a timeout
        try:
            await asyncio.wait_for(
                manager.inject_user_message(agent_name, stored_msg),
                timeout=API_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(f"Timeout injecting message to {agent_name}")
            await send_long_message(
                context.bot, update.effective_chat.id,
                f"⏱ Timed out sending message to *{agent_name}*. Please try again.",
                parse_mode="Markdown",
            )
        context.user_data.pop('talk_msg', None)
        return

    user_id = update.effective_user.id

    if data.startswith("sel_proj:"):
        proj_id = data.split(":", 1)[1]
        # Validate the project ID from callback data
        validated_id = _validate_project_id(proj_id)
        if not validated_id:
            # Predefined projects might have mixed-case names — try original
            if proj_id not in PREDEFINED_PROJECTS:
                await query.edit_message_text("❌ Invalid project name.")
                return
            validated_id = proj_id

        context.user_data['pending_switch'] = validated_id

        keyboard = [
            [
                InlineKeyboardButton("2 agents (Recommended)", callback_data="set_agents:2"),
            ],
            [
                InlineKeyboardButton("1 (Solo)", callback_data="set_agents:1"),
                InlineKeyboardButton("3 (+ Reviewer)", callback_data="set_agents:3"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Selected: *{validated_id}*\n\nHow many agents?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    elif data == "new_project":
        # User tapped "➕ New project" — prompt them to use /new
        await query.edit_message_text(
            "📝 *New Project*\n\n"
            "Use the /new command to create a project.\n"
            "It will ask you for a name, description, directory, and how many agents.",
            parse_mode="Markdown",
        )
        return

    elif data.startswith("set_agents:"):
        raw_count = data.split(":", 1)[1]
        agents_count = _validate_agents_count(raw_count)
        if agents_count is None:
            await query.edit_message_text("❌ Invalid agent count. Use 1-5.")
            return

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
                try:
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
                except PermissionError as e:
                    await query.edit_message_text(f"🚫 {e}")
                    return

                await query.edit_message_text(
                    f"✅ {'Agent' if agents_count == 1 else f'Team of {agents_count} agents'} ready for *{target}*.\n\n"
                    f"Send me a message to start!",
                    parse_mode="Markdown"
                )
            else:
                # Saved project
                saved_list = await state.session_mgr.list_projects()
                if any(p['project_id'] == target for p in saved_list):
                    data_state = await state.session_mgr.load_project(target)
                    project_dir = data_state.get("project_dir", str(PROJECTS_BASE_DIR / target))
                    project_name = data_state.get("name", target)

                    try:
                        await _activate_project(
                            user_id=user_id,
                            chat_id=chat_id,
                            bot=bot,
                            project_id=target,
                            project_name=project_name,
                            project_dir=project_dir,
                            agents_count=agents_count,
                        )
                    except PermissionError as e:
                        await query.edit_message_text(f"🚫 {e}")
                        return

                    await query.edit_message_text(
                        f"✅ Restored *{target}* with {agents_count} agent(s).\n\n"
                        f"Send me a message to continue working.",
                        parse_mode="Markdown"
                    )
                else:
                    await query.edit_message_text(f"Project '{target}' not found.")


# ============================================================
# /switch
# ============================================================

@handler_guard
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

        target = _sanitize_text(context.args[0]).lower()
        if not target:
            await update.message.reply_text("❌ Invalid project name.")
            return

        chat_id = update.effective_chat.id
        bot = context.bot

        if target in sessions:
            current_project[user_id] = target
            mgr = sessions[target]
            await update.message.reply_text(f"Switched to *{mgr.project_name}*", parse_mode="Markdown")
        elif target in PREDEFINED_PROJECTS:
            project_dir = str(Path(PREDEFINED_PROJECTS[target]).expanduser().resolve())

            agents_count = 2
            if len(context.args) > 1:
                validated = _validate_agents_count(context.args[1])
                if validated is not None:
                    agents_count = validated

            try:
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
            except PermissionError as e:
                await update.message.reply_text(f"🚫 {e}")
                return

            await update.message.reply_text(f"Switched to and initialized predefined project *{target}* in `{project_dir}`.", parse_mode="Markdown")
        else:
            saved = await state.session_mgr.list_projects()
            saved_ids = [p['project_id'] for p in saved]
            if target in saved_ids:
                data = await state.session_mgr.load_project(target)
                project_dir = data.get("project_dir", str(PROJECTS_BASE_DIR / target))
                agents_count = 2

                if len(context.args) > 1:
                    validated = _validate_agents_count(context.args[1])
                    if validated is not None:
                        agents_count = validated

                try:
                    await _activate_project(
                        user_id=user_id,
                        chat_id=chat_id,
                        bot=bot,
                        project_id=target,
                        project_name=data.get("name", target),
                        project_dir=project_dir,
                        agents_count=agents_count,
                    )
                except PermissionError as e:
                    await update.message.reply_text(f"🚫 {e}")
                    return

                await update.message.reply_text(f"Restored and switched to saved project *{target}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"Project '{target}' not found. Use /projects to see available.")


# ============================================================
# /status
# ============================================================

@handler_guard
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
            mgr_state = "running" if mgr.is_running else ("paused" if mgr.is_paused else "ready")
            lines.append(f"{marker} *{mgr.project_name}* (`{pid}`)")
            lines.append(f"  State: {mgr_state} | Turn: {mgr.turn_count}/{MAX_TURNS_PER_CYCLE}")
            lines.append(f"  Cost: ${mgr.total_cost_usd:.4f} | Agents: {', '.join(mgr.agent_names)}")

            if mgr.conversation_log:
                last = mgr.conversation_log[-1]
                lines.append(f"  Last: {last.agent_name} ({last.role})")
            lines.append("")

        lines.append("Use /switch to change your active project.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# /talk
# ============================================================

@handler_guard
async def talk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        manager = get_current_manager(user_id)

    if not manager:
        await update.message.reply_text("No active project. Use /new or /switch.")
        return

    if not context.args:
        # No args — show agent keyboard with no pre-set message
        context.user_data['talk_msg'] = None
        keyboard = [
            [
                InlineKeyboardButton("🎯 orchestrator", callback_data="talk_agent:orchestrator"),
                InlineKeyboardButton("💻 developer", callback_data="talk_agent:developer"),
            ],
            [
                InlineKeyboardButton("🔍 reviewer", callback_data="talk_agent:reviewer"),
                InlineKeyboardButton("🧪 tester", callback_data="talk_agent:tester"),
            ],
        ]
        await update.message.reply_text(
            "👇 *Select an agent to talk to:*\n\nThen send your message after selecting.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return

    # Check if first arg is a known agent name (backward compatible: /talk developer do X)
    first_arg = _validate_agent_name(context.args[0])
    if first_arg and len(context.args) >= 2:
        agent_name = first_arg
        message = _sanitize_text(" ".join(context.args[1:]))

        if not message:
            await update.message.reply_text("❌ Message cannot be empty.")
            return
        if len(message) > MAX_USER_MESSAGE_LENGTH:
            await update.message.reply_text(
                f"❌ Message too long ({len(message):,} chars). Max: {MAX_USER_MESSAGE_LENGTH:,}."
            )
            return

        await update.effective_chat.send_action(constants.ChatAction.TYPING)

        try:
            await asyncio.wait_for(
                manager.inject_user_message(agent_name, message),
                timeout=API_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await update.message.reply_text(
                f"⏱ Timed out sending to *{agent_name}*. Please try again.",
                parse_mode="Markdown",
            )
        return

    # All args are the message — show keyboard to select agent
    message = _sanitize_text(" ".join(context.args))
    if not message:
        await update.message.reply_text("❌ Message cannot be empty.")
        return

    context.user_data['talk_msg'] = message
    keyboard = [
        [
            InlineKeyboardButton("🎯 orchestrator", callback_data="talk_agent:orchestrator"),
            InlineKeyboardButton("💻 developer", callback_data="talk_agent:developer"),
        ],
        [
            InlineKeyboardButton("🔍 reviewer", callback_data="talk_agent:reviewer"),
            InlineKeyboardButton("🧪 tester", callback_data="talk_agent:tester"),
        ],
    ]
    await update.message.reply_text(
        f"👇 *Select an agent for:*\n_{message[:200]}_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# /pause
# ============================================================

@handler_guard
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
    await state.session_mgr.update_status(proj_id, "paused")
    await update.message.reply_text(f"⏸ Project *{manager.project_name}* paused.", parse_mode="Markdown")


# ============================================================
# /resume
# ============================================================

@handler_guard
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
    await state.session_mgr.update_status(proj_id, "active")
    await update.message.reply_text(f"▶️ Project *{manager.project_name}* resumed.", parse_mode="Markdown")


# ============================================================
# /stop
# ============================================================

@handler_guard
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
    await state.session_mgr.update_status(proj_id, "stopped")

    async with _state_lock:
        sessions = get_user_sessions(user_id)
        sessions.pop(proj_id, None)
        if proj_id == current_project.get(user_id):
            if sessions:
                current_project[user_id] = next(iter(sessions))
            else:
                current_project.pop(user_id, None)


# ============================================================
# /clear
# ============================================================

@handler_guard
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return

    user_id = update.effective_user.id

    async with _state_lock:
        proj_id = current_project.get(user_id)

    if not proj_id:
        await update.message.reply_text("No active project. Use /projects to select one.")
        return

    await state.session_mgr.clear_messages(proj_id)
    await update.message.reply_text(f"🗑 Cleared message history for project *{proj_id}*.", parse_mode="Markdown")


# ============================================================
# /log
# ============================================================

@handler_guard
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
    messages = await state.session_mgr.get_recent_messages(proj_id, count=15)

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
        mgr_state = "running" if manager.is_running else ("paused" if manager.is_paused else "ready")
        await update.message.reply_text(
            f"📜 *{manager.project_name}* — No messages yet.\n\n"
            f"State: {mgr_state}\n"
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


# ============================================================
# Normal text messages
# ============================================================

@handler_guard
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_auth(update):
        return
    if not await _check_rate_limit(update):
        return

    user_id = update.effective_user.id

    # Check if this is a reply to a /talk agent selection (no stored message)
    talk_target = context.user_data.pop('talk_target_agent', None)
    if talk_target:
        validated_target = _validate_agent_name(talk_target)
        if validated_target:
            async with _state_lock:
                manager = get_current_manager(user_id)
            if manager:
                raw_text = _sanitize_text(update.message.text)
                if not raw_text:
                    await update.message.reply_text("❌ Message cannot be empty.")
                    return
                if len(raw_text) > MAX_USER_MESSAGE_LENGTH:
                    await update.message.reply_text(
                        f"❌ Message too long ({len(raw_text):,} chars). Max: {MAX_USER_MESSAGE_LENGTH:,}."
                    )
                    return

                await update.effective_chat.send_action(constants.ChatAction.TYPING)
                try:
                    await asyncio.wait_for(
                        manager.inject_user_message(validated_target, raw_text),
                        timeout=API_CALL_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await update.message.reply_text(
                        f"⏱ Timed out sending to *{validated_target}*. Please try again.",
                        parse_mode="Markdown",
                    )
                return

    # Reply-to-message: auto-detect which project a message belongs to
    reply = update.message.reply_to_message
    switched_indicator = ""
    if reply and reply.message_id in _msg_to_project:
        target_project = _msg_to_project[reply.message_id]
        async with _state_lock:
            sessions = get_user_sessions(user_id)
            if target_project in sessions and current_project.get(user_id) != target_project:
                current_project[user_id] = target_project
                proj_name = sessions[target_project].project_name
                switched_indicator = f"(→ {proj_name}) "

    async with _state_lock:
        manager = get_current_manager(user_id)

    if not manager:
        # No active project — show project list automatically
        saved = await state.session_mgr.list_projects()
        if saved or PREDEFINED_PROJECTS:
            keyboard = []
            for p in saved:
                keyboard.append([InlineKeyboardButton(
                    f"💾 {p['name']}", callback_data=f"sel_proj:{p['project_id']}"
                )])
            for name in PREDEFINED_PROJECTS:
                if not any(p['project_id'] == name for p in saved):
                    keyboard.append([InlineKeyboardButton(
                        f"📁 {name}", callback_data=f"sel_proj:{name}"
                    )])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👇 *Select a project to work on:*",
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "No projects yet. Use /new to create one.",
            )
        return

    # Sanitize and validate user message
    message = _sanitize_text(update.message.text)

    if not message:
        await update.message.reply_text("❌ Message cannot be empty.")
        return

    # Reject messages that are too long to prevent abuse
    if len(message) > MAX_USER_MESSAGE_LENGTH:
        await update.message.reply_text(
            f"❌ Message too long ({len(message):,} chars). "
            f"Maximum is {MAX_USER_MESSAGE_LENGTH:,} characters."
        )
        return

    # Show typing indicator (with optional switch indicator)
    if switched_indicator:
        await update.message.reply_text(switched_indicator.strip())
    await update.effective_chat.send_action(constants.ChatAction.TYPING)

    # Update callbacks to react on THIS message
    msg_id = update.message.message_id
    proj_id = current_project.get(user_id)
    on_update, on_result, on_final = _make_callbacks(
        context.bot, update.effective_chat.id,
        user_message_id=msg_id,
        project_id=proj_id,
        project_name=manager.project_name,
    )
    manager.on_update = on_update
    manager.on_result = on_result
    manager.on_final = on_final

    if not manager.is_running:
        await manager.start_session(message)
    else:
        # Running — inject message to orchestrator
        try:
            await asyncio.wait_for(
                manager.inject_user_message("orchestrator", message),
                timeout=API_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await update.message.reply_text(
                "⏱ Timed out injecting message. The agent may be busy. Please try again shortly."
            )


# ============================================================
# Global error handler
# ============================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — catches anything that slips through handler_guard."""
    logger.error(f"Update {update} caused error: {context.error}", exc_info=context.error)
    if update and update.effective_chat:
        try:
            # Don't expose raw error details to users
            error_msg = str(context.error) if context.error else "Unknown error"
            # Truncate very long error messages
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ An error occurred. Please try again.\n\nDetails: {error_msg}",
            )
        except Exception:
            pass


# ============================================================
# Bot application builder
# ============================================================

def build_bot_application() -> Application:
    """Build and configure the Telegram bot Application (without starting it).

    This is separated from main() so the dashboard can run in the same process.
    """
    token = TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env file")

    # Initialize SDK client via shared state
    if state.sdk_client is None:
        state.sdk_client = ClaudeSDKManager()

    async def post_init(application: Application):
        # Initialize session manager via shared state
        if state.session_mgr is None:
            state.session_mgr = SessionManager()
            await state.session_mgr.initialize()

        commands = [
            BotCommand("projects", "Select a project"),
            BotCommand("status", "Project status"),
            BotCommand("health", "Bot health check"),
            BotCommand("log", "Conversation log"),
            BotCommand("talk", "Message a specific agent"),
            BotCommand("pause", "Pause agents"),
            BotCommand("resume", "Resume agents"),
            BotCommand("stop", "Stop project"),
            BotCommand("clear", "Clear history"),
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

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("projects", projects_command))
    app.add_handler(CommandHandler("switch", switch_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("talk", talk_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("log", log_command))

    # Callback query handler
    app.add_handler(CallbackQueryHandler(project_callback_handler))

    # Catch-all text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    app.add_error_handler(error_handler)

    return app


# ============================================================
# Legacy entry point
# ============================================================

def main():
    # Legacy entry point — use server.py instead
    """Run Telegram bot + FastAPI dashboard in the same async loop."""
    import uvicorn
    from dashboard.api import create_app as create_dashboard

    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))

    async def run_all():
        # Build bot
        bot_app = build_bot_application()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot started.")

        # Build dashboard
        dash = create_dashboard()
        config = uvicorn.Config(
            dash, host="0.0.0.0", port=DASHBOARD_PORT, log_level="info",
        )
        server = uvicorn.Server(config)

        logger.info(f"Dashboard starting on http://0.0.0.0:{DASHBOARD_PORT}")

        try:
            await server.serve()
        finally:
            logger.info("Shutting down...")
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()

    asyncio.run(run_all())


def _check_sandbox():
    """Warn if running inside Claude Code's macOS sandbox."""
    import platform
    if platform.system() != "Darwin":
        return

    test_dir = Path.home() / "Desktop"
    try:
        test_dir.stat()
    except PermissionError:
        logger.warning(
            "⚠️  Detected macOS sandbox (Claude Code session). "
            "The bot may not be able to access project directories outside "
            "the current working directory. For full access, start the bot "
            "from a normal Terminal window:\n"
            "  cd ~/Downloads/web-claude-bot && source venv/bin/activate && python server.py"
        )
        print(
            "\n"
            "⚠️  WARNING: Running inside Claude Code's macOS sandbox!\n"
            "   The bot cannot access other project directories.\n"
            "   To fix: open a normal Terminal window and run:\n"
            "     cd ~/Downloads/web-claude-bot && source venv/bin/activate && python server.py\n"
        )


if __name__ == "__main__":
    import platform
    if platform.system() == "Darwin":
        import subprocess as _sp
        _caffeinate = _sp.Popen(
            ["caffeinate", "-i", "-s", "-d", "-w", str(os.getpid())]
        )
        logger.info(f"caffeinate started (pid={_caffeinate.pid}) — prevents ALL sleep modes")

    _check_sandbox()
    main()
