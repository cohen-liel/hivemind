"""Background scheduler — checks for due tasks and runs them automatically."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import state

logger = logging.getLogger(__name__)


async def scheduler_loop(check_interval: int = 60):
    """Run an infinite loop that checks for due schedules every `check_interval` seconds."""
    logger.info(f"Scheduler started (checking every {check_interval}s)")
    while True:
        await asyncio.sleep(check_interval)
        try:
            await _check_due_schedules()
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)


async def _check_due_schedules():
    """Check for schedules due at the current HH:MM and trigger them."""
    if not state.session_mgr:
        return

    now = datetime.now()
    current_time = now.strftime("%H:%M")

    due = await state.session_mgr.get_due_schedules(current_time)
    if not due:
        return

    logger.info(f"Scheduler: {len(due)} schedule(s) due at {current_time}")

    for schedule in due:
        schedule_id = schedule["id"]
        project_id = schedule["project_id"]
        user_id = schedule["user_id"]
        task_desc = schedule["task_description"]
        repeat = schedule.get("repeat", "once")

        # Find or create manager
        manager, _ = state.get_manager(project_id)
        if not manager:
            logger.warning(f"Scheduler: no manager for project {project_id}, skipping schedule {schedule_id}")
            continue

        logger.info(f"Scheduler: triggering schedule {schedule_id} for project {project_id}: {task_desc[:80]}")

        try:
            if not manager.is_running:
                await manager.start_session(task_desc)
            else:
                await manager.inject_user_message("orchestrator", task_desc)

            # Mark as run
            await state.session_mgr.mark_schedule_run(schedule_id)

            # Disable one-time schedules
            if repeat == "once":
                await state.session_mgr.disable_schedule(schedule_id)
                logger.info(f"Scheduler: disabled one-time schedule {schedule_id}")

        except Exception as e:
            logger.error(f"Scheduler: failed to trigger schedule {schedule_id}: {e}")
