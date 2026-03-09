"""Main entry point — starts the web dashboard.

Usage:
    python server.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
from pathlib import Path

import uvicorn

import state
from config import PREDEFINED_PROJECTS, validate_config, ConfigError
from dashboard.api import create_app, _create_web_manager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))


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
            "project directories outside the current working directory."
        )
        print(
            "\n⚠️  WARNING: Running inside Claude Code's macOS sandbox!\n"
            "   To fix: open a normal Terminal and run:\n"
            "     cd ~/Downloads/web-claude-bot && source venv/bin/activate && python server.py\n"
        )


async def run():
    """Start web server."""
    # Initialize shared state (SDK + SessionManager)
    await state.initialize()

    # Validate configuration at startup
    try:
        warnings = validate_config()
        for w in warnings:
            logger.warning("Config: %s", w)
    except ConfigError as e:
        logger.critical("Invalid configuration: %s", e)
        raise SystemExit(1)

    # Auto-create predefined projects if they don't exist yet
    if state.session_mgr:
        for proj_name, proj_dir_raw in PREDEFINED_PROJECTS.items():
            proj_dir = os.path.expanduser(proj_dir_raw)
            try:
                dir_exists = Path(proj_dir).exists()
            except PermissionError:
                logger.info(f"Skipping predefined project '{proj_name}': no permission ({proj_dir})")
                continue
            if not dir_exists:
                logger.info(f"Skipping predefined project '{proj_name}': dir not found ({proj_dir})")
                continue
            project_id = proj_name.lower().replace(" ", "-")
            existing = await state.session_mgr.load_project(project_id)
            if not existing:
                await state.session_mgr.save_project(
                    project_id=project_id,
                    user_id=0,
                    name=proj_name,
                    description=f"Predefined project: {proj_name}",
                    project_dir=proj_dir,
                )
                logger.info(f"Created predefined project: {proj_name} -> {proj_dir}")
            # Ensure an active manager exists so we can interact immediately
            if not state.get_manager(project_id)[0]:
                manager = _create_web_manager(
                    project_id=project_id,
                    project_name=proj_name,
                    project_dir=proj_dir,
                    user_id=0,
                    agents_count=2,
                )
                if manager:
                    await state.register_manager(0, project_id, manager)
                    logger.info(f"Registered manager for predefined project: {proj_name}")

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
        """Run session cleanup and activity log trimming every hour.

        Auto-restarts on unexpected errors to ensure cleanup never stops.
        """
        while True:
            try:
                await asyncio.sleep(3600)  # 1 hour
                if state.session_mgr:
                    await state.session_mgr.cleanup_expired()
                    # Trim old activity logs to prevent unbounded growth
                    all_projects = await state.session_mgr.list_projects()
                    for proj in all_projects:
                        await state.session_mgr.cleanup_old_activity(
                            proj["project_id"], keep_last=2000
                        )
                    logger.info("Periodic cleanup: expired sessions + old activity cleaned up")
            except asyncio.CancelledError:
                raise  # Let cancellation propagate for graceful shutdown
            except Exception as e:
                logger.warning(f"Periodic cleanup error (will retry in 60s): {e}")
                await asyncio.sleep(60)  # Wait a bit before retrying

    cleanup_task = asyncio.create_task(_cleanup_loop())

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
                logger.error(f"Scheduler crashed, restarting in 30s: {e}")
                await asyncio.sleep(30)

    scheduler_task = asyncio.create_task(_resilient_scheduler())

    # Start FastAPI dashboard
    dash = create_app()
    dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    config = uvicorn.Config(
        dash, host=dashboard_host, port=DASHBOARD_PORT, log_level="info",
    )
    server = uvicorn.Server(config)

    logger.info(f"Dashboard starting on http://{dashboard_host}:{DASHBOARD_PORT}")

    try:
        await server.serve()
    finally:
        # ── Graceful shutdown (order matters!) ──
        # 1. Cancel background tasks first (they may generate events)
        logger.info("Graceful shutdown: stopping background tasks...")
        cleanup_task.cancel()
        scheduler_task.cancel()
        for bg_task in (cleanup_task, scheduler_task):
            try:
                await bg_task
            except asyncio.CancelledError:
                pass

        # 2. Save orchestrator states BEFORE stopping EventBus
        #    (save_orchestrator_state writes to DB, not EventBus)
        logger.info("Graceful shutdown: saving orchestrator states...")
        for user_id, project_id, manager in state.get_all_managers():
            if manager.is_running and state.session_mgr:
                try:
                    await state.session_mgr.save_orchestrator_state(
                        project_id=project_id,
                        user_id=user_id,
                        status="running",
                        current_loop=getattr(manager, '_current_loop', 0),
                        turn_count=manager.turn_count,
                        total_cost_usd=manager.total_cost_usd,
                        shared_context=getattr(manager, 'shared_context', []),
                        agent_states=getattr(manager, 'agent_states', {}),
                        last_user_message=getattr(manager, '_last_user_message', ''),
                    )
                    logger.info(f"  Saved state for {project_id}")
                except Exception as e:
                    logger.error(f"  Failed to save state for {project_id}: {e}")

        # 3. Stop EventBus writer AFTER state is saved
        #    (flushes any pending activity events to DB)
        logger.info("Graceful shutdown: flushing EventBus...")
        await event_bus.stop_writer()

        # 4. Close DB connection last (everything above needs it)
        if state.session_mgr:
            await state.session_mgr.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    # Prevent macOS sleep
    if platform.system() == "Darwin":
        import subprocess as _sp
        _caffeinate = _sp.Popen(
            ["caffeinate", "-i", "-s", "-d", "-w", str(os.getpid())]
        )
        logger.info(f"caffeinate started (pid={_caffeinate.pid})")

    _check_sandbox()
    asyncio.run(run())
