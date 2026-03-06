from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from config import STORE_DIR

logger = logging.getLogger(__name__)


class ConversationStore:
    def __init__(self):
        self.store_dir = STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str) -> Path:
        safe_id = project_id.replace("/", "_").replace("\\", "_")
        return self.store_dir / f"{safe_id}.json"

    def save_project(self, project_id: str, state: dict):
        path = self._project_path(project_id)
        state["updated_at"] = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False, default=str)
        logger.debug(f"Saved project state: {project_id}")

    def load_project(self, project_id: str) -> dict | None:
        path = self._project_path(project_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def add_message(self, project_id: str, agent_name: str, role: str, content: str, cost_usd: float = 0.0):
        state = self.load_project(project_id) or {
            "project_id": project_id,
            "messages": [],
            "status": "active",
            "created_at": time.time(),
        }

        state["messages"].append({
            "agent_name": agent_name,
            "role": role,
            "content": content,
            "cost_usd": cost_usd,
            "timestamp": time.time(),
        })

        self.save_project(project_id, state)

    def update_status(self, project_id: str, status: str):
        state = self.load_project(project_id)
        if state:
            state["status"] = status
            self.save_project(project_id, state)

    def list_projects(self) -> list[dict]:
        projects = []
        for path in self.store_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                projects.append({
                    "project_id": data.get("project_id", path.stem),
                    "name": data.get("name", path.stem),
                    "status": data.get("status", "unknown"),
                    "project_dir": data.get("project_dir", ""),
                    "message_count": len(data.get("messages", [])),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                })
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read {path}: {e}")
        return sorted(projects, key=lambda p: p.get("updated_at", 0), reverse=True)

    def get_recent_messages(self, project_id: str, count: int = 10) -> list[dict]:
        state = self.load_project(project_id)
        if not state:
            return []
        return state.get("messages", [])[-count:]

    def delete_project(self, project_id: str):
        path = self._project_path(project_id)
        if path.exists():
            path.unlink()
            logger.info(f"Deleted project store: {project_id}")
