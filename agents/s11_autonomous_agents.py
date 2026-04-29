#!/usr/bin/env python3
"""s11_autonomous_agents.py - autonomous teammates that find and claim work."""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import readline

    for binding in (
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ):
        try:
            readline.parse_and_bind(binding)
        except Exception:
            pass
except ImportError:
    pass

from agents.auth import make_client

WORKDIR = Path.cwd()
TEAM_DIR = WORKDIR / ".team"
TASKS_DIR = WORKDIR / ".tasks"
POLL_INTERVAL = 5.0
IDLE_TIMEOUT = 60.0

client, MODEL = make_client()

LEAD_SYSTEM = f"""You are the lead of an autonomous coding agent team at {WORKDIR}.
Teammates can find work by scanning the task board.
Use create_task to seed work, spawn_teammate to add autonomous teammates, and list_tasks/list_team to observe progress.
Use shutdown_request for graceful shutdown and review_plan for risky work.
Use end_turn when your turn is complete.
"""

TEAMMATE_SYSTEM = f"""You are an autonomous teammate in a coding agent team at {WORKDIR}.
If you have no immediate work, use idle so the harness can poll inboxes and auto-claim ready tasks.
For risky work, submit_plan before editing files.
If you receive a shutdown_request, respond with shutdown_response.
Use send_message for progress updates.
"""

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "status",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_request",
    "plan_approval_response",
}


class ProtocolRegistry:
    """Tracks request/response protocol state by request_id."""

    def __init__(self) -> None:
        self.shutdown_requests: dict[str, dict[str, Any]] = {}
        self.plan_requests: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_request_id() -> str:
        return uuid.uuid4().hex[:8]

    def create_shutdown_request(self, target: str) -> str:
        request_id = self._new_request_id()
        with self._lock:
            self.shutdown_requests[request_id] = {
                "target": target,
                "status": "pending",
                "created_at": time.time(),
            }
        return request_id

    def resolve_shutdown_request(
        self, request_id: str, approve: bool, reason: str = ""
    ) -> dict[str, Any] | None:
        with self._lock:
            request = self.shutdown_requests.get(request_id)
            if not request:
                return None
            request["status"] = "approved" if approve else "rejected"
            request["reason"] = reason
            request["updated_at"] = time.time()
            return dict(request)

    def get_shutdown_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            request = self.shutdown_requests.get(request_id)
            return dict(request) if request else None

    def create_plan_request(self, sender: str, plan: str) -> str:
        request_id = self._new_request_id()
        with self._lock:
            self.plan_requests[request_id] = {
                "from": sender,
                "plan": plan,
                "status": "pending",
                "created_at": time.time(),
            }
        return request_id

    def resolve_plan_request(
        self, request_id: str, approve: bool, feedback: str = ""
    ) -> dict[str, Any] | None:
        with self._lock:
            request = self.plan_requests.get(request_id)
            if not request:
                return None
            request["status"] = "approved" if approve else "rejected"
            request["feedback"] = feedback
            request["updated_at"] = time.time()
            return dict(request)

    def get_plan_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            request = self.plan_requests.get(request_id)
            return dict(request) if request else None

    def describe(self) -> str:
        with self._lock:
            lines = ["Protocol trackers:"]
            if self.shutdown_requests:
                lines.append("Shutdown requests:")
                for request_id, request in sorted(self.shutdown_requests.items()):
                    lines.append(
                        f"- {request_id}: target={request['target']} status={request['status']}"
                    )
            else:
                lines.append("Shutdown requests: none")

            if self.plan_requests:
                lines.append("Plan requests:")
                for request_id, request in sorted(self.plan_requests.items()):
                    lines.append(
                        f"- {request_id}: from={request['from']} status={request['status']}"
                    )
            else:
                lines.append("Plan requests: none")
            return "\n".join(lines)


class MessageBus:
    """Append-only JSONL inboxes with drain-on-read semantics."""

    def __init__(self, team_dir: Path) -> None:
        self.dir = team_dir / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.jsonl"

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(f"Invalid msg_type '{msg_type}'")

        message = {
            "type": msg_type,
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            message.update(extra)

        with self._lock:
            with self._path(to).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=True) + "\n")
        return message

    def broadcast(
        self,
        sender: str,
        recipients: list[str],
        content: str,
        msg_type: str = "broadcast",
    ) -> int:
        delivered = 0
        for recipient in recipients:
            if recipient == sender:
                continue
            self.send(sender, recipient, content, msg_type=msg_type)
            delivered += 1
        return delivered

    def drain(self, name: str) -> list[dict[str, Any]]:
        path = self._path(name)
        with self._lock:
            if not path.exists():
                return []
            raw = path.read_text(encoding="utf-8").strip()
            path.write_text("", encoding="utf-8")

        if not raw:
            return []
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    @staticmethod
    def format_messages(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "[]"
        return json.dumps(messages, indent=2, ensure_ascii=True)


class AgentState:
    """Conversation state for the lead or a teammate."""

    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role
        self.messages: list[dict[str, Any]] = []
        self.should_exit = False

    def add_message(self, role: str, content: Any) -> None:
        self.messages.append({"role": role, "content": content})

    def inject_inbox(self, inbox_messages: list[dict[str, Any]]) -> None:
        if not inbox_messages:
            return
        payload = MessageBus.format_messages(inbox_messages)
        self.add_message("user", f"<inbox>\n{payload}\n</inbox>")


class TaskBoard:
    """Task discovery and claiming helpers for autonomous teammates."""

    def __init__(self, tasks_dir: Path) -> None:
        self.dir = tasks_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _canonical_id(raw_task: dict[str, Any], path: Path) -> str:
        return str(raw_task.get("id") or path.stem.removeprefix("task_"))

    @staticmethod
    def _title(raw_task: dict[str, Any]) -> str:
        return str(raw_task.get("title") or raw_task.get("subject") or "(untitled)")

    @staticmethod
    def _dependencies(raw_task: dict[str, Any]) -> list[str]:
        deps = raw_task.get("dependencies") or raw_task.get("blockedBy") or []
        return [str(dep) for dep in deps]

    def _records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            records.append(
                {
                    "id": self._canonical_id(raw, path),
                    "title": self._title(raw),
                    "status": raw.get("status", "unknown"),
                    "owner": raw.get("owner"),
                    "dependencies": self._dependencies(raw),
                    "description": raw.get("description", ""),
                    "raw": raw,
                    "path": path,
                }
            )
        return records

    def create_task(self, title: str, description: str = "", dependencies: list[str] | None = None) -> str:
        task_id = uuid.uuid4().hex[:8]
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "status": "pending",
            "dependencies": [str(dep) for dep in (dependencies or [])],
            "owner": None,
            "created_at": time.time(),
        }
        path = self.dir / f"task_{task_id}.json"
        path.write_text(json.dumps(task, indent=2, ensure_ascii=True), encoding="utf-8")
        return f"Task created with ID: {task_id}"

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._records()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        for record in self._records():
            if record["id"] == str(task_id):
                return record
        return None

    def update_task_status(self, task_id: str, status: str) -> str:
        with self._lock:
            record = self.get_task(task_id)
            if not record:
                return f"Task {task_id} not found"
            record["raw"]["status"] = status
            record["path"].write_text(
                json.dumps(record["raw"], indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        return f"Task {task_id} status updated to {status}"

    def scan_unclaimed_tasks(self) -> list[dict[str, Any]]:
        records = self._records()
        by_id = {record["id"]: record for record in records}
        ready: list[dict[str, Any]] = []
        for record in records:
            if record["status"] != "pending" or record.get("owner"):
                continue
            deps = record.get("dependencies", [])
            if any(by_id.get(dep, {}).get("status") != "completed" for dep in deps):
                continue
            ready.append(record)
        return ready

    def claim_task(self, task_id: str, owner: str) -> str:
        with self._lock:
            record = self.get_task(task_id)
            if not record:
                return f"Error: Task {task_id} not found"
            if record["raw"].get("owner"):
                existing_owner = record["raw"].get("owner") or "someone else"
                return f"Error: Task {task_id} has already been claimed by {existing_owner}"
            if record["raw"].get("status") != "pending":
                status = record["raw"].get("status")
                return f"Error: Task {task_id} cannot be claimed because its status is '{status}'"
            deps = self._dependencies(record["raw"])
            if deps:
                records = {item["id"]: item for item in self._records()}
                blocked = [dep for dep in deps if records.get(dep, {}).get("status") != "completed"]
                if blocked:
                    return (
                        f"Error: Task {task_id} is blocked by unfinished dependency(s): "
                        + ", ".join(blocked)
                    )
            record["raw"]["owner"] = owner
            record["raw"]["status"] = "in_progress"
            record["path"].write_text(
                json.dumps(record["raw"], indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        return f"Claimed task #{task_id} for {owner}"

    def render(self) -> str:
        records = self._records()
        if not records:
            return "(no tasks)"
        lines = []
        for record in records:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(record["status"], "[?]")
            owner = f" @{record['owner']}" if record.get("owner") else ""
            deps = ", ".join(record.get("dependencies", [])) or "-"
            lines.append(f"{marker} #{record['id']}: {record['title']}{owner} deps={deps}")
        return "\n".join(lines)


class TeammateManager:
    """Persistent roster and autonomous lifecycle manager for teammates."""

    def __init__(
        self,
        team_dir: Path,
        bus: MessageBus | None = None,
        protocols: ProtocolRegistry | None = None,
        tasks: TaskBoard | None = None,
    ) -> None:
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self._lock = threading.RLock()
        self.bus = bus or MessageBus(team_dir)
        self.protocols = protocols or ProtocolRegistry()
        self.tasks = tasks or TaskBoard(TASKS_DIR)
        self.threads: dict[str, threading.Thread] = {}
        self.config = self._load_config()
        self.ensure_member("lead", "team lead", status="idle")

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        config = {"team_name": "default", "members": []}
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config

    def _save_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def ensure_member(self, name: str, role: str, status: str = "idle") -> dict[str, Any]:
        with self._lock:
            for member in self.config["members"]:
                if member["name"] == name:
                    if role and member.get("role") != role:
                        member["role"] = role
                    if status:
                        member["status"] = status
                    self._save_config()
                    return member

            member = {
                "name": name,
                "role": role,
                "status": status,
                "created_at": time.time(),
            }
            self.config["members"].append(member)
            self._save_config()
            return member

    def list_members(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(member) for member in self.config["members"]]

    def describe_team(self) -> str:
        members = self.list_members()
        if not members:
            return "No teammates registered."
        lines = [f"Team: {self.config.get('team_name', 'default')}"]
        for member in members:
            lines.append(f"- {member['name']} ({member['role']}): {member['status']}")
        return "\n".join(lines)

    def set_status(self, name: str, status: str) -> None:
        with self._lock:
            for member in self.config["members"]:
                if member["name"] == name:
                    member["status"] = status
                    member["updated_at"] = time.time()
                    self._save_config()
                    return

    def spawn(self, name: str, role: str, prompt: str) -> str:
        if name == "lead":
            return "Error: teammate name 'lead' is reserved"

        with self._lock:
            for member in self.config["members"]:
                if member["name"] == name and member.get("status") not in (
                    "idle",
                    "shutdown",
                ):
                    return f"Error: teammate '{name}' is already {member['status']}"

            self.ensure_member(name, role, status="working")

        print(f"[team] spawning {name} ({role})")
        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"Spawned teammate '{name}' ({role})"

    def _teammate_loop(self, name: str, role: str, prompt: str) -> None:
        state = AgentState(name=name, role=role)
        team_name = self.config.get("team_name", "default")
        state.add_message(
            "user",
            (
                f"You are teammate '{name}' with role '{role}'.\n"
                f"Initial assignment: {prompt}\n"
                "When you run out of work, use idle so the harness can look for inbox messages or task-board work."
            ),
        )
        system = (
            f"{TEAMMATE_SYSTEM}\n"
            f"Your teammate name is {name}. Your role is {role}. Team name is {team_name}."
        )
        tools = build_tools(role="teammate")
        handlers = make_tool_handlers(
            state=state,
            manager=self,
            bus=self.bus,
            protocols=self.protocols,
            tasks=self.tasks,
            role="teammate",
        )

        try:
            while not state.should_exit:
                self.set_status(name, "working")
                _, stop_tool = agent_loop(
                    state=state,
                    system=system,
                    tools=tools,
                    tool_handlers=handlers,
                    poll_inbox=True,
                    bus=self.bus,
                    stop_tools={"idle", "shutdown_response"},
                    max_iterations=50,
                )
                if state.should_exit:
                    break

                self.set_status(name, "idle")
                if not self._idle_poll(state, role=role, team_name=team_name):
                    state.should_exit = True
                    break
                if stop_tool != "idle":
                    continue
        finally:
            self.set_status(name, "shutdown" if state.should_exit else "idle")
            print(f"[team] {name} is now {'shutdown' if state.should_exit else 'idle'}")

    def _idle_poll(
        self,
        state: AgentState,
        role: str,
        team_name: str,
        poll_interval: float | None = None,
        idle_timeout: float | None = None,
    ) -> bool:
        poll_interval = POLL_INTERVAL if poll_interval is None else poll_interval
        idle_timeout = IDLE_TIMEOUT if idle_timeout is None else idle_timeout
        attempts = max(1, math.ceil(idle_timeout / max(poll_interval, 0.01)))

        for attempt in range(attempts):
            if attempt > 0 and poll_interval > 0:
                time.sleep(poll_interval)

            inbox_messages = self.bus.drain(state.name)
            if inbox_messages:
                if any(msg.get("type") == "shutdown_request" for msg in inbox_messages):
                    state.inject_inbox(inbox_messages)
                    return True
                state.inject_inbox(inbox_messages)
                return True

            unclaimed = self.tasks.scan_unclaimed_tasks()
            if unclaimed:
                task = unclaimed[0]
                result = self.tasks.claim_task(task["id"], state.name)
                if result.startswith("Error:"):
                    continue
                reinject_identity_if_needed(state, team_name)
                task_prompt = (
                    f"<auto-claimed>Task #{task['id']}: {task['title']}\n"
                    f"{task.get('description', '')}</auto-claimed>"
                )
                state.add_message("user", task_prompt)
                state.add_message("assistant", f"I claimed task #{task['id']}. Continuing.")
                return True

        return False

    def join_all(self, timeout: float | None = None) -> None:
        for thread in list(self.threads.values()):
            thread.join(timeout=timeout)


BUS = MessageBus(TEAM_DIR)
PROTOCOLS = ProtocolRegistry()
TASKS = TaskBoard(TASKS_DIR)
MANAGER = TeammateManager(TEAM_DIR, bus=BUS, protocols=PROTOCOLS, tasks=TASKS)


def reinject_identity_if_needed(state: AgentState, team_name: str) -> None:
    if len(state.messages) > 3:
        return
    identity = make_identity_block(state.name, state.role, team_name)
    if state.messages and state.messages[0] == identity:
        return
    state.messages.insert(0, identity)
    state.messages.insert(1, {"role": "assistant", "content": f"I am {state.name}. Continuing."})


def make_identity_block(name: str, role: str, team_name: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            f"<identity>You are '{name}', role: {role}, team: {team_name}. "
            "Continue your work.</identity>"
        ),
    }


def is_dangerous_command(command: str) -> bool:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    return any(token in command for token in dangerous)


def safe_path(path_text: str) -> Path:
    path = (WORKDIR / path_text).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_text}")
    return path


def run_bash(command: str) -> str:
    if is_dangerous_command(command):
        return "Error: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as exc:
        return f"Error: {exc}"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        text = safe_path(path).read_text(encoding="utf-8")
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"


def extract_text(content_blocks: list[Any]) -> str:
    texts = [block.text for block in content_blocks if hasattr(block, "text")]
    return "\n".join(texts).strip()


def render_task_list(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "No tasks found."
    return json.dumps([task["raw"] for task in tasks], indent=2, ensure_ascii=True)


def build_tools(role: str = "lead") -> list[dict[str, Any]]:
    tools = [
        {
            "name": "bash",
            "description": "Run a shell command (blocking).",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
        {
            "name": "send_message",
            "description": "Send a message to one teammate or the lead.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {"type": "string", "enum": sorted(VALID_MSG_TYPES)},
                },
                "required": ["to", "content"],
            },
        },
        {
            "name": "read_inbox",
            "description": "Read your inbox, or if you are lead, optionally another inbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            },
        },
        {
            "name": "get_task",
            "description": "Get task details by id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "list_tasks",
            "description": "List all tasks on the board.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "update_task_status",
            "description": "Update a task status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["task_id", "status"],
            },
        },
    ]

    if role == "lead":
        tools.extend(
            [
                {
                    "name": "end_turn",
                    "description": "Finish the current turn with a final response.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "response": {"type": "string"},
                        },
                        "required": ["response"],
                    },
                },
                {
                    "name": "spawn_teammate",
                    "description": "Spawn an autonomous teammate with a role and seed prompt.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                        "required": ["name", "role", "prompt"],
                    },
                },
                {
                    "name": "broadcast_message",
                    "description": "Broadcast a message to the whole team except the sender.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                },
                {
                    "name": "list_team",
                    "description": "List current team members and their status.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "shutdown_request",
                    "description": "Request a teammate to shut down gracefully.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "teammate": {"type": "string"},
                        },
                        "required": ["teammate"],
                    },
                },
                {
                    "name": "check_shutdown",
                    "description": "Check the status of a shutdown request by request_id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                        },
                        "required": ["request_id"],
                    },
                },
                {
                    "name": "review_plan",
                    "description": "Approve or reject a teammate plan by request_id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "feedback": {"type": "string"},
                        },
                        "required": ["request_id", "approve"],
                    },
                },
                {
                    "name": "check_plan",
                    "description": "Check the status of a plan request by request_id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                        },
                        "required": ["request_id"],
                    },
                },
                {
                    "name": "create_task",
                    "description": "Create a task for autonomous teammates.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["title"],
                    },
                },
            ]
        )
    else:
        tools.extend(
            [
                {
                    "name": "shutdown_response",
                    "description": "Approve or reject a shutdown request using its request_id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["request_id", "approve"],
                    },
                },
                {
                    "name": "submit_plan",
                    "description": "Submit a plan to lead for approval before risky work.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "plan": {"type": "string"},
                        },
                        "required": ["plan"],
                    },
                },
                {
                    "name": "idle",
                    "description": "Signal that you have no more work and want to enter idle polling.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "claim_task",
                    "description": "Claim a ready task from the task board by id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                        },
                        "required": ["task_id"],
                    },
                },
            ]
        )

    return tools


def make_tool_handlers(
    state: AgentState,
    manager: TeammateManager,
    bus: MessageBus,
    protocols: ProtocolRegistry,
    tasks: TaskBoard,
    role: str = "lead",
) -> dict[str, Any]:
    def send_message(**kw: Any) -> str:
        msg_type = kw.get("msg_type", "message")
        message = bus.send(state.name, kw["to"], kw["content"], msg_type=msg_type)
        return f"Sent {message['type']} from {message['from']} to {message['to']}"

    def read_inbox(**kw: Any) -> str:
        target = kw.get("name") or state.name
        if state.name != "lead" and target != state.name:
            return f"Error: {state.name} can only read its own inbox"
        messages = bus.drain(target)
        return MessageBus.format_messages(messages)

    def get_task(**kw: Any) -> str:
        task = tasks.get_task(kw["task_id"])
        return json.dumps(task["raw"], indent=2, ensure_ascii=True) if task else f"Task {kw['task_id']} not found"

    def list_tasks(**_: Any) -> str:
        return render_task_list(tasks.list_tasks())

    def update_task_status(**kw: Any) -> str:
        return tasks.update_task_status(kw["task_id"], kw["status"])

    handlers: dict[str, Any] = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "send_message": send_message,
        "read_inbox": read_inbox,
        "get_task": get_task,
        "list_tasks": list_tasks,
        "update_task_status": update_task_status,
    }

    if role == "lead":
        def end_turn(**kw: Any) -> str:
            return kw["response"]

        def spawn_teammate(**kw: Any) -> str:
            if state.name != "lead":
                return "Error: only the lead can spawn teammates"
            return manager.spawn(kw["name"], kw["role"], kw["prompt"])

        def broadcast_message(**kw: Any) -> str:
            recipients = [member["name"] for member in manager.list_members()]
            delivered = bus.broadcast(state.name, recipients, kw["content"])
            return f"Broadcast delivered to {delivered} teammates"

        def list_team(**_: Any) -> str:
            return manager.describe_team()

        def shutdown_request(**kw: Any) -> str:
            teammate = kw["teammate"]
            request_id = protocols.create_shutdown_request(teammate)
            bus.send(
                "lead",
                teammate,
                "Please shut down gracefully.",
                "shutdown_request",
                {"request_id": request_id},
            )
            return f"Shutdown request {request_id} sent to '{teammate}' (status: pending)"

        def check_shutdown(**kw: Any) -> str:
            request = protocols.get_shutdown_request(kw["request_id"])
            return json.dumps(request or {"error": "not found"}, ensure_ascii=True)

        def review_plan(**kw: Any) -> str:
            request_id = kw["request_id"]
            request = protocols.resolve_plan_request(
                request_id,
                kw["approve"],
                kw.get("feedback", ""),
            )
            if not request:
                return f"Error: Unknown plan request_id '{request_id}'"
            bus.send(
                "lead",
                request["from"],
                kw.get("feedback", ""),
                "plan_approval_response",
                {
                    "request_id": request_id,
                    "approve": kw["approve"],
                    "feedback": kw.get("feedback", ""),
                },
            )
            return f"Plan {request['status']} for '{request['from']}'"

        def check_plan(**kw: Any) -> str:
            request = protocols.get_plan_request(kw["request_id"])
            return json.dumps(request or {"error": "not found"}, ensure_ascii=True)

        def create_task(**kw: Any) -> str:
            return tasks.create_task(
                kw["title"],
                description=kw.get("description", ""),
                dependencies=kw.get("dependencies", []),
            )

        handlers.update(
            {
                "end_turn": end_turn,
                "spawn_teammate": spawn_teammate,
                "broadcast_message": broadcast_message,
                "list_team": list_team,
                "shutdown_request": shutdown_request,
                "check_shutdown": check_shutdown,
                "review_plan": review_plan,
                "check_plan": check_plan,
                "create_task": create_task,
            }
        )
    else:
        def shutdown_response(**kw: Any) -> str:
            request_id = kw["request_id"]
            request = protocols.resolve_shutdown_request(
                request_id,
                kw["approve"],
                kw.get("reason", ""),
            )
            if not request:
                return f"Error: Unknown shutdown request_id '{request_id}'"
            bus.send(
                state.name,
                "lead",
                kw.get("reason", ""),
                "shutdown_response",
                {
                    "request_id": request_id,
                    "approve": kw["approve"],
                    "reason": kw.get("reason", ""),
                },
            )
            if kw["approve"]:
                state.should_exit = True
            return f"Shutdown {'approved' if kw['approve'] else 'rejected'}"

        def submit_plan(**kw: Any) -> str:
            plan = kw["plan"]
            request_id = protocols.create_plan_request(state.name, plan)
            bus.send(
                state.name,
                "lead",
                plan,
                "plan_approval_request",
                {
                    "request_id": request_id,
                    "plan": plan,
                },
            )
            return f"Plan submitted (request_id={request_id}). Waiting for lead approval."

        def idle(**_: Any) -> str:
            return "Entering idle phase. Will poll inbox and task board."

        def claim_task(**kw: Any) -> str:
            return tasks.claim_task(kw["task_id"], state.name)

        handlers.update(
            {
                "shutdown_response": shutdown_response,
                "submit_plan": submit_plan,
                "idle": idle,
                "claim_task": claim_task,
            }
        )

    return handlers


def agent_loop(
    state: AgentState,
    system: str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    max_iterations: int = 20,
    poll_inbox: bool = False,
    bus: MessageBus | None = None,
    stop_tools: set[str] | None = None,
) -> tuple[str, str | None]:
    last_response = ""
    stop_tools = stop_tools or set()

    for round_no in range(1, max_iterations + 1):
        if poll_inbox and bus is not None:
            inbox_messages = bus.drain(state.name)
            if inbox_messages:
                print(f"[{state.name}] inbox {len(inbox_messages)} message(s)")
                state.inject_inbox(inbox_messages)

        print(f"[{state.name}] round {round_no}: requesting model")
        response = client.messages.create(
            model=MODEL,
            system=system,
            messages=state.messages,
            tools=tools,
            max_tokens=4000,
        )
        state.add_message("assistant", response.content)

        if response.stop_reason != "tool_use":
            last_response = extract_text(response.content)
            return last_response, None

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            handler = tool_handlers.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
            except Exception as exc:
                output = f"Error: {exc}"

            print(f"[{state.name} tool] {block.name}")
            print(f"[{state.name} result] {str(output)[:200]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )
            if block.name in stop_tools:
                return str(output), block.name

        state.add_message("user", results)

    return last_response, None


def run_lead_turn(task: str, state: AgentState | None = None) -> tuple[str, AgentState]:
    state = state or AgentState(name="lead", role="team lead")
    state.add_message("user", task)
    result, _ = agent_loop(
        state=state,
        system=LEAD_SYSTEM,
        tools=build_tools(role="lead"),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=MANAGER,
            bus=BUS,
            protocols=PROTOCOLS,
            tasks=TASKS,
            role="lead",
        ),
        poll_inbox=True,
        bus=BUS,
        stop_tools={"end_turn"},
    )
    return result, state


def main() -> None:
    lead_state = AgentState(name="lead", role="team lead")

    while True:
        try:
            query = input("\033[36ms11 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip() == "/team":
            print(MANAGER.describe_team())
            print()
            continue
        if query.strip() == "/inbox":
            print(MessageBus.format_messages(BUS.drain("lead")))
            print()
            continue
        if query.strip() == "/protocols":
            print(PROTOCOLS.describe())
            print()
            continue
        if query.strip() == "/tasks":
            print(TASKS.render())
            print()
            continue

        result, lead_state = run_lead_turn(query, state=lead_state)
        if result:
            print(result)
        print()


if __name__ == "__main__":
    main()
