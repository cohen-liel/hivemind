"""Main entry point — starts the web dashboard.

Usage:
    python server.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sqlite3
import time
from pathlib import Path

import uvicorn

# Configure structured logging FIRST — before any other module imports so
# that every logger created at import time inherits the correct handlers.
from logging_config import configure_logging

configure_logging()

import state
from config import (
    DB_VACUUM_INTERVAL_HOURS,
    GRACEFUL_STOP_TIMEOUT,
    HEALTH_CHECK_INTERVAL,
    SCHEDULER_RETRY_DELAY,
    ConfigError,
    validate_config,
)
from dashboard.api import create_app
from plugin_registry import registry as plugin_registry

logger = logging.getLogger(__name__)

DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))


def _find_cloudflared() -> str | None:
    """Return path to cloudflared binary, or None if not found."""
    import shutil

    candidates = [
        shutil.which("cloudflared"),
        str(Path.home() / ".local" / "bin" / "cloudflared"),
        "/usr/local/bin/cloudflared",
        "/opt/homebrew/bin/cloudflared",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def _migrate_db_add_project_dir(db_path: str) -> None:
    """One-time migration: add project_dir column to projects table if missing."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(projects)")]
        if "project_dir" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN project_dir TEXT NOT NULL DEFAULT ''")
            conn.commit()
            print("[migration] Added project_dir column to projects table")
    finally:
        conn.close()


def _check_sandbox():
    """Warn if running inside Claude Code's macOS sandbox."""
    if platform.system() != "Darwin":
        return
    test_dir = Path.home() / "Desktop"
    try:
        test_dir.stat()
    except PermissionError:
        logger.warning(
            "⚠️  Detected macOS sandbox. The bot may not be able to access "
            "project directories outside the current working directory. "
            "To fix: open a normal Terminal and run: "
            "open a normal Terminal, cd into this project, and run: python server.py"
        )


def _install_global_exception_handler():
    """Install a global asyncio exception handler that suppresses known SDK bugs.

    The claude_agent_sdk uses anyio internally, and when a generator is GC'd
    in a different asyncio task than it was created in, anyio raises:
        RuntimeError('Attempted to exit cancel scope in a different task')

    This is harmless (the agent already finished) but pollutes logs with scary
    tracebacks. We suppress it here at the event loop level.
    """
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()

    def _handler(loop, context):
        exception = context.get("exception")
        if exception and isinstance(exception, RuntimeError):
            msg = str(exception)
            if "cancel scope" in msg and "different task" in msg:
                logger.debug(
                    "Suppressed orphaned anyio cancel scope error (harmless SDK cleanup): %s",
                    context.get("message", ""),
                )
                return  # Suppress — don't log the scary traceback
        # For all other exceptions, use the original handler or default
        if original_handler:
            original_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    logger.info("Installed global asyncio exception handler (suppresses anyio cancel scope leaks)")


async def run():
    """Start web server."""
    # Install global exception handler FIRST — before any SDK calls
    _install_global_exception_handler()

    # Run DB migrations before anything else touches the database
    _db_dir = Path(__file__).resolve().parent / "data"
    for _db_name in ("bot.db", "platform.db"):
        _migrate_db_add_project_dir(str(_db_dir / _db_name))

    # Initialize shared state (SDK + PlatformSessionManager)
    await state.initialize()

    # Validate configuration at startup
    try:
        warnings = validate_config()
        for w in warnings:
            logger.warning("Config: %s", w)
    except ConfigError as e:
        logger.critical("Invalid configuration: %s", e)
        raise SystemExit(1)

    # Discover and hot-reload plugins
    _loaded_plugins = plugin_registry.discover()
    logger.info(
        "Plugin registry: loaded %d plugin(s) at startup: %s", len(_loaded_plugins), _loaded_plugins
    )
    plugin_registry.start_hot_reload()

    # Connect EventBus to session manager for activity persistence
    from dashboard.events import event_bus

    if state.session_mgr:
        event_bus.set_session_manager(state.session_mgr)
        await event_bus.start_writer()
        logger.info("EventBus DB writer connected")

    # Check for interrupted tasks from previous crash
    if state.session_mgr:
        interrupted = await state.session_mgr.get_interrupted_tasks()
        if interrupted:
            logger.info(f"Found {len(interrupted)} interrupted task(s) from previous session")
            for task_state in interrupted:
                pid = task_state["project_id"]
                pname = task_state.get("project_name", pid)
                loop_num = task_state.get("current_loop", 0)
                cost = task_state.get("total_cost_usd", 0)
                logger.info(
                    f"  Interrupted: {pname} (loop {loop_num}, ${cost:.2f}) "
                    f"- last message: {task_state.get('last_user_message', '')[:80]}"
                )
                # Mark as interrupted (not running) so user can manually resume
                await state.session_mgr.save_orchestrator_state(
                    project_id=pid,
                    user_id=task_state.get("user_id", 0),
                    status="interrupted",
                    current_loop=loop_num,
                    turn_count=task_state.get("turn_count", 0),
                    total_cost_usd=cost,
                    last_user_message=task_state.get("last_user_message", ""),
                )

    # Start periodic cleanup task (with auto-restart on crash)
    async def _cleanup_loop():
        """Run session cleanup and activity log trimming periodically.

        Uses CLEANUP_INTERVAL and CLEANUP_KEEP_LAST_ACTIVITY from config (ARCH-08 fix).
        Auto-restarts on unexpected errors to ensure cleanup never stops.
        """
        from config import CLEANUP_INTERVAL, CLEANUP_KEEP_LAST_ACTIVITY

        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                if state.session_mgr:
                    await state.session_mgr.cleanup_expired()
                    # Trim old activity logs to prevent unbounded growth
                    all_projects = await state.session_mgr.list_projects()
                    for proj in all_projects:
                        await state.session_mgr.cleanup_old_activity(
                            proj["project_id"], keep_last=CLEANUP_KEEP_LAST_ACTIVITY
                        )
                    logger.info("Periodic cleanup: expired sessions + old activity cleaned up")
            except asyncio.CancelledError:
                raise  # Let cancellation propagate for graceful shutdown
            except Exception as e:
                logger.warning(f"Periodic cleanup error (will retry in 60s): {e}", exc_info=True)
                await asyncio.sleep(SCHEDULER_RETRY_DELAY)  # Wait before retrying

    cleanup_task = asyncio.create_task(_cleanup_loop())

    # Start periodic state file writer — writes a JSON snapshot of all projects
    # so the user can always see what's happening (even without the UI)
    state_file = Path("state_snapshot.json")
    _last_snapshot_hash: str = ""  # track changes to avoid redundant writes

    async def _state_writer():
        """Write a JSON state snapshot periodically, but only if state changed."""
        nonlocal _last_snapshot_hash
        import hashlib
        import json as _json

        from config import STATE_WRITER_INTERVAL

        while True:
            try:
                await asyncio.sleep(STATE_WRITER_INTERVAL)
                snapshot = {
                    "timestamp": time.time(),
                    "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "projects": {},
                }
                managers = state.get_all_managers()
                for _user_id, project_id, manager in managers:
                    proj_state = {
                        "status": "running"
                        if manager.is_running
                        else ("paused" if manager.is_paused else "idle"),
                        "turn_count": manager.turn_count,
                        "total_cost_usd": round(manager.total_cost_usd, 4),
                        "current_agent": manager.current_agent,
                        "pending_messages": manager.pending_message_count,
                        "agent_states": {},
                    }
                    for agent_name, agent_st in dict(manager.agent_states).items():
                        proj_state["agent_states"][agent_name] = {
                            "state": agent_st.get("state", "idle"),
                            "task": agent_st.get("task", ""),
                            "current_tool": agent_st.get("current_tool", ""),
                        }
                    snapshot["projects"][project_id] = proj_state

                # Always include idle projects from DB so the frontend
                # can see them even when no managers are active.
                if state.session_mgr:
                    all_projects = await state.session_mgr.list_projects()
                    for p in all_projects:
                        pid = p["project_id"]
                        if pid not in snapshot["projects"]:
                            snapshot["projects"][pid] = {
                                "status": "idle",
                                "name": p.get("name", pid),
                            }

                # Skip write if nothing changed (compare hash excluding timestamp)
                content_for_hash = {
                    k: v for k, v in snapshot.items() if k != "timestamp" and k != "timestamp_human"
                }
                current_hash = hashlib.sha256(
                    _json.dumps(content_for_hash, sort_keys=True, default=str).encode()
                ).hexdigest()
                if current_hash == _last_snapshot_hash:
                    continue
                _last_snapshot_hash = current_hash

                await asyncio.to_thread(
                    state_file.write_text, _json.dumps(snapshot, indent=2, default=str)
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"State writer error (will retry in 10s): {e}", exc_info=True)
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    state_writer_task = asyncio.create_task(_state_writer())

    # Start task scheduler (with auto-restart on crash)
    from scheduler import scheduler_loop

    async def _resilient_scheduler():
        """Wrapper that restarts the scheduler if it crashes unexpectedly."""
        while True:
            try:
                await scheduler_loop(check_interval=60)
            except asyncio.CancelledError:
                raise  # Let cancellation propagate for graceful shutdown
            except Exception as e:
                logger.error(f"Scheduler crashed, restarting in 30s: {e}", exc_info=True)
                await asyncio.sleep(HEALTH_CHECK_INTERVAL * 3)  # Extended delay

    scheduler_task = asyncio.create_task(_resilient_scheduler())

    # Start periodic VACUUM task (runs weekly by default)
    async def _vacuum_loop():
        """Run VACUUM periodically to reclaim space and defragment the database.

        Checks whether enough time has elapsed since the last VACUUM before
        running (based on DB_VACUUM_INTERVAL_HOURS).
        """
        interval_seconds = DB_VACUUM_INTERVAL_HOURS * 3600
        # Wait one interval before the first check
        await asyncio.sleep(min(interval_seconds, 3600))  # At most 1h before first check
        while True:
            try:
                if state.session_mgr:
                    last = await state.session_mgr.get_last_vacuum()
                    cutoff = time.time() - interval_seconds
                    if last is None or last < cutoff:
                        logger.info("Running scheduled VACUUM…")
                        await state.session_mgr.vacuum()
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise  # Let cancellation propagate for graceful shutdown
            except Exception as e:
                logger.warning(f"VACUUM error (will retry next cycle): {e}")
                await asyncio.sleep(SCHEDULER_RETRY_DELAY * 60)  # Retry in 1h on error

    vacuum_task = asyncio.create_task(_vacuum_loop())

    # Start FastAPI dashboard
    dash = create_app()
    dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    config = uvicorn.Config(
        dash,
        host=dashboard_host,
        port=DASHBOARD_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # ── Auto-start Cloudflare Tunnel (remote / phone access) ──────────────────
    tunnel_proc: asyncio.subprocess.Process | None = None
    cloudflared_path = os.getenv("CLOUDFLARED_PATH") or _find_cloudflared()

    if cloudflared_path:

        async def _start_tunnel() -> None:
            nonlocal tunnel_proc
            try:
                tunnel_proc = await asyncio.create_subprocess_exec(
                    cloudflared_path,
                    "tunnel",
                    "--url",
                    f"http://localhost:{DASHBOARD_PORT}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                logger.info("🌐 Cloudflare Tunnel starting...")
                if tunnel_proc.stdout is None:
                    raise RuntimeError("Tunnel process stdout is not available")
                tunnel_url_found = False
                async for raw_line in tunnel_proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not tunnel_url_found and ("trycloudflare.com" in line or "https://" in line):
                        import re

                        m = re.search(r"https://[^\s\"']+\.trycloudflare\.com", line)
                        if m:
                            url = m.group(0)
                            tunnel_url_found = True
                            logger.info("")
                            logger.info("=" * 60)
                            logger.info("🌍 PUBLIC ACCESS URL (use from any device):")
                            logger.info("")
                            logger.info(f"   {url}")
                            logger.info("")
                            logger.info("   Open this link on your phone, laptop,")
                            logger.info("   or any device with internet access.")
                            logger.info("=" * 60)
                            # Print QR for the tunnel URL (scannable from anywhere)
                            try:
                                from terminal_qr import print_qr_for_url

                                print("  📱 Scan to open on any device:")
                                print_qr_for_url(url)
                            except Exception:
                                pass
                            logger.info("")
                    # suppress verbose cloudflared debug lines
            except FileNotFoundError:
                logger.warning("cloudflared binary not found — tunnel not started")
            except Exception as e:
                logger.warning(f"Tunnel error: {e}")

        tunnel_task = asyncio.create_task(_start_tunnel())
    else:
        logger.info("")
        logger.info("ℹ️  Remote access not available (cloudflared not installed).")
        logger.info("   To access from anywhere, run: ./setup.sh")
        logger.info("   It will install cloudflared automatically.")
        logger.info("")
        tunnel_task = None
    # ─────────────────────────────────────────────────────────────────────────────

    # Show access URLs
    import socket

    lan_url: str | None = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        lan_url = f"http://{lan_ip}:{DASHBOARD_PORT}"
        logger.info(f"🏠 LAN access:   {lan_url}")
    except OSError:
        logger.debug("LAN IP detection unavailable", exc_info=True)

    logger.info(f"🌐 Local access: http://localhost:{DASHBOARD_PORT}")

    # ── Print device access code ──────────────────────────────────────────────
    # Re-use the same DeviceAuthManager instance created by dashboard/api.py
    # so the access code matches what the verify endpoint checks.
    from device_auth import DeviceAuthManager

    _auth_mgr = DeviceAuthManager()
    _auth_mgr.print_access_code()

    # ── QR code for mobile access ─────────────────────────────────────────────
    # Print a scannable QR code with the LAN URL so users can quickly open
    # the dashboard on their phone.  Only the URL is embedded — NO credentials.
    # The device auth flow (access code → device token) handles authentication.
    qr_url = lan_url or f"http://localhost:{DASHBOARD_PORT}"
    try:
        from terminal_qr import print_qr_for_url

        print("  📱 Scan to open on your phone:")
        print_qr_for_url(qr_url)
    except Exception:
        pass  # QR is a nice-to-have, never block startup

    print(flush=True)

    try:
        await server.serve()
    finally:
        # ── Graceful shutdown (order matters!) ──
        # 0. Stop plugin hot-reload watcher
        plugin_registry.stop_hot_reload()

        # 1. Stop tunnel
        if tunnel_task:
            tunnel_task.cancel()
            try:
                await tunnel_task
            except asyncio.CancelledError:
                pass
        if tunnel_proc and tunnel_proc.returncode is None:
            tunnel_proc.terminate()

        # 2. Cancel background tasks first (they may generate events)
        logger.info("Graceful shutdown: stopping background tasks...")
        cleanup_task.cancel()
        scheduler_task.cancel()
        state_writer_task.cancel()
        vacuum_task.cancel()
        for bg_task in (cleanup_task, scheduler_task, state_writer_task, vacuum_task):
            try:
                await bg_task
            except asyncio.CancelledError:
                pass

        # 2. Stop all EventBus heartbeats (they publish events we no longer need)
        logger.info("Graceful shutdown: stopping heartbeats...")
        await event_bus.stop_all_heartbeats()

        # 3. Save orchestrator states BEFORE stopping them
        #    (stop() sets is_running=False, so we must save while still True)
        logger.info("Graceful shutdown: saving orchestrator states...")
        for user_id, project_id, manager in await state.get_all_managers_safe():
            if manager.is_running and state.session_mgr:
                try:
                    await state.session_mgr.save_orchestrator_state(
                        project_id=project_id,
                        user_id=user_id,
                        status="interrupted",
                        current_loop=getattr(manager, "_current_loop", 0),
                        turn_count=manager.turn_count,
                        total_cost_usd=manager.total_cost_usd,
                        shared_context=getattr(manager, "shared_context", []),
                        agent_states=getattr(manager, "agent_states", {}),
                        last_user_message=getattr(manager, "_last_user_message", ""),
                    )
                    logger.info(f"  Saved state for {project_id}")
                except Exception as e:
                    logger.error(f"  Failed to save state for {project_id}: {e}", exc_info=True)

        # 4. Stop running orchestrators gracefully (AFTER state is saved)
        logger.info("Graceful shutdown: stopping active orchestrators...")
        for _user_id, project_id, manager in await state.get_all_managers_safe():
            if manager.is_running:
                try:
                    await asyncio.wait_for(manager.stop(), timeout=GRACEFUL_STOP_TIMEOUT)
                    logger.info(f"  Stopped orchestrator for {project_id}")
                except TimeoutError:
                    logger.warning(f"  Timeout stopping orchestrator for {project_id}")
                except Exception as e:
                    logger.error(
                        f"  Error stopping orchestrator for {project_id}: {e}", exc_info=True
                    )

        # 5. Stop EventBus writer AFTER state is saved
        #    (flushes any pending activity events to DB)
        logger.info("Graceful shutdown: flushing EventBus...")
        await event_bus.stop_writer()

        # 6. Create database backup before closing connection
        if state.session_mgr:
            try:
                backup_path = await state.session_mgr.create_backup()
                logger.info(f"  Shutdown backup saved: {backup_path}")
            except Exception as e:
                logger.error(f"  Shutdown backup failed: {e}", exc_info=True)

        # 7. Close DB connection last (everything above needs it)
        if state.session_mgr:
            await state.session_mgr.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    # Prevent macOS sleep
    if platform.system() == "Darwin":
        import subprocess as _sp

        _caffeinate = _sp.Popen(["caffeinate", "-i", "-s", "-d", "-w", str(os.getpid())])
        logger.info(f"caffeinate started (pid={_caffeinate.pid})")

    _check_sandbox()
    asyncio.run(run())
