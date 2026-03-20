"""Execution control endpoints — message sending, queue management, pause/resume/stop,
schedules, budget, and the WebSocket real-time event stream.

This router owns all endpoints related to running, controlling, and monitoring
the execution lifecycle of projects.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import state
from dashboard.events import event_bus
from dashboard.routers import (
    CreateScheduleRequest,
    SendMessageRequest,
    SetBudgetRequest,
    _create_web_manager,
    _db_event_to_dict,
    _find_manager,
    _get_or_create_manager_lock,
    _problem,
    _valid_project_id,
)

logger = logging.getLogger("dashboard.api")

router = APIRouter(tags=["execution"])


@router.post("/api/projects/{project_id}/message")
async def send_message(project_id: str, req: SendMessageRequest):
    """Send message to orchestrator via the parallel task queue.

    Returns instantly with queue position and estimated wait time so the
    frontend can show feedback before any agent work starts.  Also emits
    a MESSAGE_QUEUED WebSocket event via the EventBus.
    """
    from src.api.websocket_handler import build_message_queued_event, build_task_queued_event
    from src.workers.task_queue import TaskQueueRegistry
    from src.workers.task_worker import process_message_task

    logger.info("[%s] Received message for task queue: %s", project_id, req.message[:100])

    project_name: str | None = None
    project_dir: str | None = None
    user_id: int = 0

    manager, _uid = await _find_manager(project_id)
    if manager:
        project_name = manager.project_name
        project_dir = manager.project_dir
        user_id = _uid or 0
    elif state.session_mgr:
        _proj_lock = await _get_or_create_manager_lock(project_id)
        async with _proj_lock:
            manager, _uid = await _find_manager(project_id)
            if manager:
                project_name = manager.project_name
                project_dir = manager.project_dir
                user_id = _uid or 0
            else:
                db_project = await state.session_mgr.load_project(project_id)
                if db_project:
                    project_name = db_project.get("name", project_id)
                    project_dir = db_project.get("project_dir", "")
                    user_id = db_project.get("user_id") or 0
                    status_manager = _create_web_manager(
                        project_id=project_id,
                        project_name=project_name,
                        project_dir=project_dir,
                        user_id=user_id,
                        agents_count=2,
                    )
                    if status_manager:
                        await state.register_manager(user_id, project_id, status_manager)
                        logger.info("[%s] Status manager registered from DB", project_id)

    if project_name is None:
        logger.error("[%s] No project found — cannot enqueue message", project_id)
        return _problem(404, "Project not found.")

    registry = TaskQueueRegistry.get_registry()
    task_queue = await registry.get_or_create_queue(project_id)

    record = await task_queue.enqueue(
        message=req.message,
        worker_fn=process_message_task,
        project_name=project_name,
        project_dir=project_dir or "",
        user_id=user_id,
        mode=req.mode,
    )

    queue_position = task_queue.queue_position_of(record.task_id)
    est_wait = task_queue.estimated_wait_seconds(queue_position)

    logger.info(
        "[%s] Message enqueued as task_id=%s (queue_depth=%d, running=%d/%d, pos=%d, est_wait=%.1fs)",
        project_id,
        record.task_id,
        task_queue.queue_depth,
        task_queue.running_count,
        task_queue.max_concurrent,
        queue_position,
        est_wait,
    )

    # Emit MESSAGE_QUEUED event via EventBus for real-time frontend feedback
    msg_queued_event = build_message_queued_event(
        project_id=project_id,
        task_id=record.task_id,
        message_preview=req.message,
        queue_position=queue_position,
        queue_depth=task_queue.queue_depth,
        running_count=task_queue.running_count,
        max_concurrent=task_queue.max_concurrent,
        estimated_wait_seconds=est_wait,
    )
    await event_bus.publish(msg_queued_event)

    # If there are already running tasks, also emit TASK_QUEUED so the
    # frontend can show the queued indicator immediately
    if task_queue.running_count > 0 and queue_position > 0:
        task_queued_event = build_task_queued_event(
            project_id=project_id,
            message_preview=req.message,
            queue_position=queue_position,
            queue_depth=task_queue.queue_depth,
            running_graphs=task_queue.running_count,
            max_concurrent_graphs=task_queue.max_concurrent,
        )
        await event_bus.publish(task_queued_event)

    return {
        "ok": True,
        "task_id": record.task_id,
        "status": record.status.value,
        "queue_depth": task_queue.queue_depth,
        "queue_position": queue_position,
        "estimated_wait_seconds": est_wait,
    }


@router.post("/api/projects/{project_id}/queue")
async def enqueue_message(project_id: str, req: SendMessageRequest):
    """Add a message to the project's persistent queue."""
    if not state.session_mgr:
        return _problem(503, "Session manager unavailable.")
    manager, _ = await _find_manager(project_id)
    if not manager:
        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return _problem(404, "Project not found.")

    if manager and not manager.is_running:
        await manager.start_session(req.message)
        return {"ok": True, "action": "started", "queue_size": 0}

    queue_size = await state.session_mgr.enqueue_message(project_id, req.message)
    if manager:
        await manager.inject_user_message("orchestrator", req.message)
    logger.info(f"[{project_id}] Queued message (position {queue_size}): {req.message[:80]}")
    return {"ok": True, "action": "queued", "position": queue_size, "queue_size": queue_size}


@router.get("/api/projects/{project_id}/queue")
async def get_queue(project_id: str):
    """Return structured queue state: pending messages, active tasks, and estimates.

    First checks the task queue (primary source of truth for in-flight work),
    then falls back to the session manager's persistent queue for messages
    that haven't been promoted to tasks yet.
    """
    from src.workers.task_queue import TaskQueueRegistry

    # Try task queue first — it has richer state
    registry = TaskQueueRegistry.get_registry()
    task_queue = await registry.get_queue(project_id)
    if task_queue:
        queue_state = await task_queue.get_queue_state()
        return {"ok": True, "project_id": project_id, **queue_state}

    # Fall back to session manager persistent queue
    if not state.session_mgr:
        return _problem(503, "Session manager unavailable.")
    items = await state.session_mgr.list_queued_messages(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "pending_tasks": [
            {
                "message_preview": (item.get("message", "") or "")[:100],
                "queue_position": idx,
                "created_at": item.get("created_at"),
            }
            for idx, item in enumerate(items, 1)
        ],
        "active_tasks": [],
        "queue_depth": len(items),
        "running_count": 0,
        "max_concurrent": 5,
        "estimated_drain_seconds": 0.0,
    }


@router.delete("/api/projects/{project_id}/queue/{msg_id}")
async def delete_queued_message(project_id: str, msg_id: int):
    """Remove a specific message from the queue by ID."""
    if not state.session_mgr:
        return _problem(503, "Session manager unavailable.")
    deleted = await state.session_mgr.delete_queued_message(project_id, msg_id)
    if not deleted:
        return _problem(404, f"Message {msg_id} not found in queue.")
    return {"ok": True, "deleted_id": msg_id}


@router.delete("/api/projects/{project_id}/queue")
async def clear_queue(project_id: str):
    """Clear all queued messages for a project."""
    if not state.session_mgr:
        return _problem(503, "Session manager unavailable.")
    count = await state.session_mgr.clear_queue(project_id)
    logger.info(f"[{project_id}] Cleared {count} queued message(s)")
    return {"ok": True, "cleared": count}


@router.post("/api/projects/{project_id}/pause")
async def pause_project(project_id: str):
    """Pause project."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active")
    manager.pause()
    if state.session_mgr:
        await state.session_mgr.update_status(project_id, "paused")
    await event_bus.publish(
        {
            "type": "project_status",
            "project_id": project_id,
            "status": "paused",
        }
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/resume")
async def resume_project(project_id: str):
    """Resume project."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active")
    manager.resume()
    if state.session_mgr:
        await state.session_mgr.update_status(project_id, "active")
    await event_bus.publish(
        {
            "type": "project_status",
            "project_id": project_id,
            "status": "running",
        }
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/stop")
async def stop_project(project_id: str):
    """Stop project."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active")
    await manager.stop()
    if state.session_mgr:
        await state.session_mgr.update_status(project_id, "stopped")
    await event_bus.publish(
        {
            "type": "project_status",
            "project_id": project_id,
            "status": "stopped",
        }
    )
    return {"ok": True}


# --- Schedules CRUD ---


@router.get("/api/schedules")
async def list_schedules(user_id: int = 0):
    """List all schedules for a user."""
    if not state.session_mgr:
        return {"schedules": []}
    schedules = await state.session_mgr.get_schedules(user_id)
    return {"schedules": schedules}


@router.post("/api/schedules")
async def create_schedule(req: CreateScheduleRequest):
    """Create a new schedule."""
    if not state.session_mgr:
        return _problem(500, "DB not ready")
    if not re.match(r"^\d{2}:\d{2}$", req.schedule_time):
        return _problem(400, "schedule_time must be HH:MM format")
    h, m = int(req.schedule_time[:2]), int(req.schedule_time[3:])
    if h > 23 or m > 59:
        return _problem(400, "Invalid time: hours 0-23, minutes 0-59")
    schedule_id = await state.session_mgr.add_schedule(
        user_id=req.user_id,
        project_id=req.project_id,
        schedule_time=req.schedule_time,
        task_description=req.task_description,
        repeat=req.repeat,
    )
    return {"ok": True, "schedule_id": schedule_id}


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, user_id: int = 0):
    """Delete a schedule."""
    if not state.session_mgr:
        return _problem(500, "DB not ready")
    deleted = await state.session_mgr.delete_schedule(schedule_id, user_id)
    if not deleted:
        return _problem(404, "Schedule not found")
    return {"ok": True}


@router.put("/api/projects/{project_id}/budget")
async def set_project_budget(project_id: str, req: SetBudgetRequest):
    """Set per-project budget cap."""
    if not state.session_mgr:
        return _problem(500, "DB not ready")
    await state.session_mgr.set_project_budget(project_id, req.budget_usd)
    return {"ok": True, "budget_usd": req.budget_usd}


# --- WebSocket ---


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket for real-time event stream with ping/pong heartbeat.

    Supports event replay on reconnect, first-frame authentication,
    conversation history loading, and task status polling.
    """
    from config import (
        AUTH_ENABLED,
        DASHBOARD_API_KEY,
        DEVICE_AUTH_ENABLED,
        WS_AUTH_TIMEOUT,
    )
    from device_auth import COOKIE_NAME, DeviceAuthManager
    from src.api.websocket_handler import (
        get_or_create_conversation_id,
        load_history_on_connect,
        start_new_conversation,
    )

    _device_auth = DeviceAuthManager()
    ws_id = uuid.uuid4().hex[:12]

    await ws.accept()

    # --- Authentication ---
    if not DEVICE_AUTH_ENABLED and not (AUTH_ENABLED and DASHBOARD_API_KEY):
        logger.debug("WebSocket [%s]: auth disabled — skipping", ws_id)
    elif not DEVICE_AUTH_ENABLED and AUTH_ENABLED and DASHBOARD_API_KEY:
        try:
            raw = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT)
            if not isinstance(raw, dict) or raw.get("type") != "auth":
                await ws.send_json(
                    {"type": "auth_failed", "reason": "First message must be an auth frame"}
                )
                await ws.close(code=4003, reason="Unauthorized: expected auth frame")
                return
            client_key = raw.get("api_key", "")
            if not client_key or client_key != DASHBOARD_API_KEY:
                await ws.send_json({"type": "auth_failed", "reason": "Invalid API key"})
                await ws.close(code=4003, reason="Unauthorized: invalid key")
                return
            await ws.send_json({"type": "auth_ok"})
            logger.info("WebSocket [%s]: authenticated via legacy api_key", ws_id)
        except TimeoutError:
            try:
                await ws.send_json({"type": "auth_failed", "reason": "Auth timeout"})
                await ws.close(code=4003, reason="Unauthorized: auth timeout")
            except (ConnectionError, RuntimeError):
                pass
            return
        except WebSocketDisconnect:
            return
        except Exception:
            try:
                await ws.close(code=4003, reason="Auth error")
            except (ConnectionError, RuntimeError):
                pass
            return
    else:
        try:
            cookie_token = ""
            try:
                ws_cookies = ws.cookies if hasattr(ws, "cookies") else {}
                cookie_token = ws_cookies.get(COOKIE_NAME, "")
            except Exception:
                pass
            if not cookie_token:
                raw_cookie = ""
                for hdr_name, hdr_val in ws.headers.raw:
                    if hdr_name == b"cookie":
                        raw_cookie = hdr_val.decode("utf-8", errors="replace")
                        break
                if raw_cookie:
                    import http.cookies

                    c = http.cookies.SimpleCookie()
                    try:
                        c.load(raw_cookie)
                        if COOKIE_NAME in c:
                            cookie_token = c[COOKIE_NAME].value
                    except Exception:
                        pass
            if cookie_token and _device_auth.verify_device_token(cookie_token):
                await ws.send_json({"type": "auth_ok"})
                logger.info("WebSocket [%s]: authenticated via device cookie", ws_id)
            else:
                raw = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT)
                if not isinstance(raw, dict) or raw.get("type") != "auth":
                    await ws.send_json(
                        {"type": "auth_failed", "reason": "First message must be an auth frame"}
                    )
                    await ws.close(code=4003, reason="Unauthorized: expected auth frame")
                    return
                client_token = raw.get("device_token", "") or raw.get("api_key", "")
                if not isinstance(client_token, str) or not client_token:
                    await ws.send_json({"type": "auth_failed", "reason": "Missing device_token"})
                    await ws.close(code=4003, reason="Unauthorized: token required")
                    return
                if not _device_auth.verify_device_token(client_token):
                    await ws.send_json({"type": "auth_failed", "reason": "Invalid device token"})
                    await ws.close(code=4003, reason="Unauthorized: invalid token")
                    return
                await ws.send_json({"type": "auth_ok"})
                logger.info("WebSocket [%s]: authenticated via first-frame device token", ws_id)
        except TimeoutError:
            try:
                await ws.send_json({"type": "auth_failed", "reason": "Auth timeout"})
                await ws.close(code=4003, reason="Unauthorized: auth timeout")
            except (ConnectionError, RuntimeError):
                pass
            return
        except WebSocketDisconnect:
            logger.info("WebSocket [%s]: client disconnected during auth", ws_id)
            return
        except Exception as _auth_err:
            logger.debug("WebSocket [%s]: auth error — %s", ws_id, _auth_err)
            try:
                await ws.close(code=4003, reason="Auth error")
            except (ConnectionError, RuntimeError):
                pass
            return

    client_info = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
    logger.info("WebSocket [%s]: accepted connection from %s", ws_id, client_info)
    queue = await event_bus.subscribe()

    async def _sender():
        """Forward events from bus to WebSocket client."""
        try:
            while True:
                try:
                    from config import WS_SENDER_TIMEOUT

                    event = await asyncio.wait_for(queue.get(), timeout=float(WS_SENDER_TIMEOUT))
                    await ws.send_json(event)
                except TimeoutError:
                    logger.debug(
                        "WebSocket [%s]: sender idle for %ss — closing stale connection",
                        ws_id,
                        WS_SENDER_TIMEOUT,
                    )
                    break
                except WebSocketDisconnect:
                    logger.info("WebSocket [%s]: client disconnected during send", ws_id)
                    break
                except Exception as _send_err:
                    logger.warning("WebSocket [%s]: send error — %s", ws_id, _send_err)
                    break
        except asyncio.CancelledError:
            raise

    async def _heartbeat():
        """Send periodic pings to detect stale connections."""
        from config import WS_HEARTBEAT_INTERVAL

        while True:
            await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
            try:
                await ws.send_json({"type": "ping"})
            except WebSocketDisconnect:
                logger.info("WebSocket [%s]: client disconnected during heartbeat", ws_id)
                break
            except Exception as _ping_err:
                logger.debug("WebSocket [%s]: heartbeat ping failed — %s", ws_id, _ping_err)
                break

    async def _receiver():
        """Listen for client messages (pong, replay requests, future commands)."""
        while True:
            try:
                data = await ws.receive_json()
                if not isinstance(data, dict):
                    continue
                msg_type = data.get("type", "")
                if not isinstance(msg_type, str) or len(msg_type) > 50:
                    continue

                if msg_type == "pong":
                    pass

                elif msg_type == "auth":
                    await ws.send_json({"type": "auth_ok"})

                elif msg_type == "replay":
                    project_id = data.get("project_id", "")
                    since_seq = data.get("since_sequence", 0)
                    if not isinstance(project_id, str) or not _valid_project_id(project_id):
                        continue
                    if not isinstance(since_seq, int) or since_seq < 0:
                        since_seq = 0
                    if project_id:
                        events = event_bus.get_buffered_events(project_id, since_seq)
                        if not events and state.session_mgr:
                            db_events = await state.session_mgr.get_activity_since(
                                project_id, since_seq, limit=200
                            )
                            events = [_db_event_to_dict(e, project_id) for e in db_events]
                        await ws.send_json(
                            {
                                "type": "replay_batch",
                                "project_id": project_id,
                                "events": events,
                                "latest_sequence": event_bus.get_latest_sequence(project_id),
                            }
                        )
                        logger.debug(
                            "WebSocket [%s]: replayed %d events for project %s since seq %d",
                            ws_id,
                            len(events),
                            project_id,
                            since_seq,
                        )

                elif msg_type == "get_history":
                    project_id = data.get("project_id", "")
                    limit = data.get("limit", 200)
                    if not isinstance(project_id, str) or not _valid_project_id(project_id):
                        continue
                    if not isinstance(limit, int) or limit < 1:
                        limit = 200
                    limit = min(limit, 1000)
                    try:
                        _conv_id = await get_or_create_conversation_id(project_id)
                        _history = await load_history_on_connect(project_id, _conv_id, limit=limit)
                        await ws.send_json(
                            {
                                "type": "conversation_history",
                                "project_id": project_id,
                                "conversation_id": _conv_id,
                                "messages": _history,
                                "count": len(_history),
                            }
                        )
                        logger.info(
                            "WebSocket [%s]: sent %d history messages for project %s conv %s",
                            ws_id,
                            len(_history),
                            project_id,
                            _conv_id,
                        )
                    except Exception as _hist_err:
                        logger.error(
                            "WebSocket [%s]: failed to load history for project %s: %s",
                            ws_id,
                            project_id,
                            _hist_err,
                        )
                        await ws.send_json(
                            {
                                "type": "error",
                                "code": "HISTORY_LOAD_FAILED",
                                "message": "Failed to load conversation history.",
                                "project_id": project_id,
                            }
                        )

                elif msg_type == "new_conversation":
                    project_id = data.get("project_id", "")
                    title = data.get("title", None)
                    if not isinstance(project_id, str) or not _valid_project_id(project_id):
                        continue
                    if title is not None and not isinstance(title, str):
                        title = None
                    try:
                        _conv_id = await start_new_conversation(project_id, title=title)
                        await ws.send_json(
                            {
                                "type": "conversation_created",
                                "project_id": project_id,
                                "conversation_id": _conv_id,
                            }
                        )
                        logger.info(
                            "WebSocket [%s]: new conversation %s for project %s",
                            ws_id,
                            _conv_id,
                            project_id,
                        )
                    except Exception as _new_conv_err:
                        logger.error(
                            "WebSocket [%s]: failed to create conversation for project %s: %s",
                            ws_id,
                            project_id,
                            _new_conv_err,
                        )

                elif msg_type == "get_task_status":
                    project_id = data.get("project_id", "")
                    task_id_req = data.get("task_id", "")
                    if not isinstance(project_id, str) or not _valid_project_id(project_id):
                        continue
                    if not isinstance(task_id_req, str) or not task_id_req.isalnum():
                        continue
                    try:
                        from src.workers.task_queue import TaskQueueRegistry as _TQR

                        _tq_registry = _TQR.get_registry()
                        _task_queue = await _tq_registry.get_queue(project_id)
                        if _task_queue:
                            _task_rec = await _task_queue.get_task(task_id_req)
                            if _task_rec:
                                await ws.send_json({"type": "task_status", **_task_rec.to_dict()})
                                continue
                        await ws.send_json(
                            {
                                "type": "error",
                                "code": "TASK_NOT_FOUND",
                                "task_id": task_id_req,
                                "message": "Task not found.",
                            }
                        )
                    except Exception as _ts_err:
                        logger.warning("WebSocket [%s]: get_task_status error: %s", ws_id, _ts_err)

                elif msg_type == "get_task_history":
                    project_id = data.get("project_id", "")
                    task_id_req = data.get("task_id", "")
                    limit = data.get("limit", 200)
                    if not isinstance(project_id, str) or not _valid_project_id(project_id):
                        continue
                    if not isinstance(task_id_req, str) or not task_id_req.isalnum():
                        continue
                    if not isinstance(limit, int) or limit < 1:
                        limit = 200
                    limit = min(limit, 1000)
                    try:
                        from src.workers.task_queue import TaskQueueRegistry as _TQR

                        _tq_registry = _TQR.get_registry()
                        _task_queue = await _tq_registry.get_queue(project_id)
                        _task_conv_id = None
                        if _task_queue:
                            _task_rec = await _task_queue.get_task(task_id_req)
                            if _task_rec:
                                _task_conv_id = _task_rec.conversation_id
                        if _task_conv_id:
                            _task_history = await load_history_on_connect(
                                project_id, _task_conv_id, limit=limit
                            )
                            await ws.send_json(
                                {
                                    "type": "task_history",
                                    "project_id": project_id,
                                    "task_id": task_id_req,
                                    "conversation_id": _task_conv_id,
                                    "messages": _task_history,
                                    "count": len(_task_history),
                                }
                            )
                        else:
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "code": "TASK_CONV_NOT_READY",
                                    "task_id": task_id_req,
                                    "message": "Task conversation not yet created (still queued?).",
                                }
                            )
                    except Exception as _th_err:
                        logger.error(
                            "WebSocket [%s]: get_task_history error: %s",
                            ws_id,
                            _th_err,
                            exc_info=True,
                        )

                else:
                    logger.debug("WebSocket [%s]: unknown message type %r", ws_id, msg_type)

            except WebSocketDisconnect:
                logger.info("WebSocket [%s]: client disconnected (receive)", ws_id)
                break
            except Exception as _recv_err:
                logger.warning("WebSocket [%s]: receive error — %s", ws_id, _recv_err)
                break

    try:
        await asyncio.gather(_sender(), _heartbeat(), _receiver())
    except asyncio.CancelledError:
        logger.info("WebSocket [%s]: handler cancelled (server shutdown)", ws_id)
        raise
    except WebSocketDisconnect:
        logger.info("WebSocket [%s]: disconnected", ws_id)
    except Exception as e:
        logger.error("WebSocket [%s]: unexpected error — %s", ws_id, e, exc_info=True)
        try:
            await ws.send_json(
                {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected server error occurred. Please reconnect.",
                    "reconnect_after_ms": 2000,
                }
            )
        except Exception as _ws_close_err:
            logger.debug(
                "WebSocket [%s]: error frame suppressed (connection already broken): %s",
                ws_id,
                _ws_close_err,
            )
    finally:
        await event_bus.unsubscribe(queue)
        await event_bus._flush_write_queue()
        logger.info("WebSocket [%s]: connection closed, unsubscribed from event bus", ws_id)
