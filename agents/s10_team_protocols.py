#!/usr/bin/env python3
"""s10_team_protocols.py - structured request/response protocols for agent teams."""

from __future__ import annotations

import json
import os
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
client, MODEL = make_client()


LEAD_SYSTEM = f"""You are the team lead for a protocol-driven coding agent team at {WORKDIR}.
Use spawn_teammate to create teammates.
Use shutdown_request when you want a graceful shutdown handshake.
Review risky work by responding to submit_plan requests with review_plan.
Use check_shutdown and check_plan to inspect request state.
Use read_inbox and list_team to monitor the team.
Use end_turn when your turn is complete.
"""

TEAMMATE_SYSTEM = f"""You are a persistent teammate in a protocol-driven coding agent team at {WORKDIR}.
For risky or major work, submit a plan with submit_plan before making changes.
If you receive a shutdown_request, respond with shutdown_response.
Use send_message to report status updates.
Use end_turn when your current assignment is complete.
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
    """Tracks request/response state machines by request_id."""

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


class TeammateManager:
    """Persistent roster and lifecycle manager for teammates."""

    def __init__(
        self,
        team_dir: Path,
        bus: MessageBus | None = None,
        protocols: ProtocolRegistry | None = None,
    ) -> None:
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self._lock = threading.RLock()
        self.bus = bus or MessageBus(team_dir)
        self.protocols = protocols or ProtocolRegistry()
        self.threads: dict[str, threading.Thread] = {}
        self.config = self._load_config()
        self.ensure_member("lead", "team lead", status="idle")

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        config = {"members": []}
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
        lines = ["Team roster:"]
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
        state.add_message(
            "user",
            (
                f"You are teammate '{name}' with role '{role}'.\n"
                f"Assignment: {prompt}\n"
                "For major changes, submit a plan and wait for approval before editing files."
            ),
        )

        try:
            system = (
                f"{TEAMMATE_SYSTEM}\n"
                f"Your teammate name is {name}. Your role is {role}."
            )
            tools = build_tools(role="teammate")
            handlers = make_tool_handlers(
                state=state,
                manager=self,
                bus=self.bus,
                protocols=self.protocols,
                role="teammate",
            )

            result = agent_loop(
                state=state,
                system=system,
                tools=tools,
                tool_handlers=handlers,
                poll_inbox=True,
                bus=self.bus,
            )
            if result and not state.should_exit:
                self.bus.send(
                    sender=name,
                    to="lead",
                    content=result,
                    msg_type="status",
                    extra={"status": "completed"},
                )

            # Teammates stay alive after their first assignment so the lead can send
            # later protocol messages like graceful shutdown requests.
            while not state.should_exit:
                self.set_status(name, "idle")
                inbox_messages = self.bus.drain(name)
                if not inbox_messages:
                    time.sleep(0.1)
                    continue

                self.set_status(name, "working")
                state.inject_inbox(inbox_messages)
                result = agent_loop(
                    state=state,
                    system=system,
                    tools=tools,
                    tool_handlers=handlers,
                    poll_inbox=False,
                    bus=self.bus,
                )
                if result and not state.should_exit:
                    self.bus.send(
                        sender=name,
                        to="lead",
                        content=result,
                        msg_type="status",
                        extra={"status": "completed"},
                    )
        finally:
            self.set_status(name, "shutdown" if state.should_exit else "idle")
            print(f"[team] {name} is now {'shutdown' if state.should_exit else 'idle'}")

    def join_all(self, timeout: float | None = None) -> None:
        for thread in list(self.threads.values()):
            thread.join(timeout=timeout)


BUS = MessageBus(TEAM_DIR)
PROTOCOLS = ProtocolRegistry()
MANAGER = TeammateManager(TEAM_DIR, bus=BUS, protocols=PROTOCOLS)


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
    ]

    if role == "lead":
        tools.extend(
            [
                {
                    "name": "spawn_teammate",
                    "description": "Spawn a persistent teammate with a role and assignment.",
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
                    "description": "Request a teammate to shut down gracefully. Returns a request_id.",
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
            ]
        )

    return tools


def make_tool_handlers(
    state: AgentState,
    manager: TeammateManager,
    bus: MessageBus,
    protocols: ProtocolRegistry,
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

    def end_turn(**kw: Any) -> str:
        return kw["response"]

    handlers: dict[str, Any] = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "send_message": send_message,
        "read_inbox": read_inbox,
        "end_turn": end_turn,
    }

    if role == "lead":
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

        handlers.update(
            {
                "spawn_teammate": spawn_teammate,
                "broadcast_message": broadcast_message,
                "list_team": list_team,
                "shutdown_request": shutdown_request,
                "check_shutdown": check_shutdown,
                "review_plan": review_plan,
                "check_plan": check_plan,
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

        handlers.update(
            {
                "shutdown_response": shutdown_response,
                "submit_plan": submit_plan,
            }
        )

    return handlers


def extract_text(content_blocks: list[Any]) -> str:
    texts = [block.text for block in content_blocks if hasattr(block, "text")]
    return "\n".join(texts).strip()


def agent_loop(
    state: AgentState,
    system: str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    max_iterations: int = 20,
    poll_inbox: bool = False,
    bus: MessageBus | None = None,
) -> str:
    last_response = ""

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
            return last_response

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
            if block.name == "end_turn":
                return str(output)
            if block.name == "shutdown_response" and state.should_exit:
                return str(output)

        state.add_message("user", results)

    return last_response


def run_lead_turn(task: str, state: AgentState | None = None) -> tuple[str, AgentState]:
    state = state or AgentState(name="lead", role="team lead")
    state.add_message("user", task)
    result = agent_loop(
        state=state,
        system=LEAD_SYSTEM,
        tools=build_tools(role="lead"),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=MANAGER,
            bus=BUS,
            protocols=PROTOCOLS,
            role="lead",
        ),
        poll_inbox=True,
        bus=BUS,
    )
    return result, state


def main() -> None:
    lead_state = AgentState(name="lead", role="team lead")

    while True:
        try:
            query = input("\033[36ms10 >> \033[0m")
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

        result, lead_state = run_lead_turn(query, state=lead_state)
        if result:
            print(result)
        print()


if __name__ == "__main__":
    main()
