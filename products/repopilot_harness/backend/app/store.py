from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from .util import atomic_write_json, now_ts, read_json


class JsonStore:
    def __init__(self, directory: Path, prefix: str):
        self.directory = directory
        self.prefix = prefix
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, item_id: str) -> Path:
        return self.directory / f"{self.prefix}_{item_id}.json"

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            atomic_write_json(self._path(str(data["id"])), data)

    def get(self, item_id: str) -> dict[str, Any]:
        return read_json(self._path(str(item_id)))

    def list(self) -> list[dict[str, Any]]:
        items = [read_json(path) for path in sorted(self.directory.glob(f"{self.prefix}_*.json"))]
        items.sort(key=lambda item: item.get("created_at", 0))
        return items


class RunStore(JsonStore):
    def __init__(self, root: Path):
        super().__init__(root / "runs", "run")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = {
            "id": uuid.uuid4().hex[:10],
            "status": "draft",
            "created_at": now_ts(),
            "updated_at": now_ts(),
            **payload,
        }
        self.save(run)
        return run


class TaskStore(JsonStore):
    def __init__(self, root: Path):
        super().__init__(root / "tasks", "task")
        self._sequence_path = root / "tasks" / ".sequence"
        if not self._sequence_path.exists():
            self._sequence_path.write_text("0", encoding="utf-8")

    def _next_id(self) -> int:
        with self._lock:
            current = int(self._sequence_path.read_text(encoding="utf-8").strip() or "0")
            current += 1
            self._sequence_path.write_text(str(current), encoding="utf-8")
            return current

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = {
            "id": self._next_id(),
            "status": "todo",
            "created_at": now_ts(),
            "updated_at": now_ts(),
            **payload,
        }
        self.save(task)
        return task


class JobStore(JsonStore):
    def __init__(self, root: Path):
        super().__init__(root / "jobs", "job")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = {
            "id": uuid.uuid4().hex[:10],
            "status": "queued",
            "created_at": now_ts(),
            "updated_at": now_ts(),
            **payload,
        }
        self.save(job)
        return job


class WorktreeStore(JsonStore):
    def __init__(self, root: Path):
        super().__init__(root / "worktrees", "worktree")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:10],
            "status": "active",
            "created_at": now_ts(),
            "updated_at": now_ts(),
            **payload,
        }
        self.save(item)
        return item


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        self._lock = threading.RLock()
        self._listeners: set[queue.Queue[dict[str, Any]]] = set()

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {
            "id": uuid.uuid4().hex[:12],
            "type": event_type,
            "ts": now_ts(),
            **payload,
        }
        encoded = json.dumps(event, ensure_ascii=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(event)
        return event

    def list(self, run_id: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        items: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 5000)) :]:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if run_id and item.get("run_id") != run_id:
                continue
            items.append(item)
        return items

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        listener: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._listeners.add(listener)
        return listener

    def unsubscribe(self, listener: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._listeners.discard(listener)
