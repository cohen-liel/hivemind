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

    # Start periodic cleanup task
    async def _cleanup_loop():
        """Run session cleanup every hour."""
        while True:
            await asyncio.sleep(3600)  # 1 hour
            try:
                if state.session_mgr:
                    await state.session_mgr.cleanup_expired()
                    logger.info("Periodic cleanup: expired sessions cleaned up")
            except Exception as e:
                logger.warning(f"Periodic cleanup error: {e}")

    cleanup_task = asyncio.create_task(_cleanup_loop())

    # Start task scheduler
    from scheduler import scheduler_loop
    scheduler_task = asyncio.create_task(scheduler_loop(check_interval=60))

    # Start FastAPI dashboard
    dash = create_app()
    config = uvicorn.Config(
        dash, host="0.0.0.0", port=DASHBOARD_PORT, log_level="info",
    )
    server = uvicorn.Server(config)

    logger.info(f"Dashboard starting on http://0.0.0.0:{DASHBOARD_PORT}")

    try:
        await server.serve()
    finally:
        cleanup_task.cancel()
        scheduler_task.cancel()
        for task in (cleanup_task, scheduler_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
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
