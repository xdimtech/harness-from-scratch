#!/usr/bin/env python3
"""s09_agent_teams.py - persistent teammates with JSONL inboxes."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
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


LEAD_SYSTEM = f"""You are the team lead for a coding agent team at {WORKDIR}.
Delegate independent work with spawn_teammate.
Use send_message or broadcast_message to coordinate teammates.
Use read_inbox to inspect messages sent back to you.
Use list_team to review teammate status.
Use end_turn when your turn is complete.
"""

TEAMMATE_SYSTEM = f"""You are a persistent teammate in a coding agent team at {WORKDIR}.
Check your inbox when needed, do focused work, and report progress with send_message.
Use end_turn when your current assignment is complete.
"""


def is_dangerous_command(command: str) -> bool:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    return any(token in command for token in dangerous)


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
    """Conversation state for one lead or teammate agent."""

    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role
        self.messages: list[dict[str, Any]] = []

    def add_message(self, role: str, content: Any) -> None:
        self.messages.append({"role": role, "content": content})

    def inject_inbox(self, inbox_messages: list[dict[str, Any]]) -> None:
        if not inbox_messages:
            return
        payload = MessageBus.format_messages(inbox_messages)
        self.add_message("user", f"<inbox>\n{payload}\n</inbox>")


class TeammateManager:
    """Persistent roster and lifecycle manager for teammates."""

    def __init__(self, team_dir: Path, bus: MessageBus | None = None) -> None:
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self._lock = threading.RLock()
        self.bus = bus or MessageBus(team_dir)
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
            lines.append(
                f"- {member['name']} ({member['role']}): {member['status']}"
            )
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
                if member["name"] == name and member.get("status") == "working":
                    return f"Error: teammate '{name}' is already working"

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
                "When useful, message lead or other teammates with send_message."
            ),
        )

        try:
            result = agent_loop(
                state=state,
                system=(
                    f"{TEAMMATE_SYSTEM}\n"
                    f"Your teammate name is {name}. Your role is {role}."
                ),
                tools=build_tools(can_spawn=False),
                tool_handlers=make_tool_handlers(
                    state=state,
                    manager=self,
                    bus=self.bus,
                    can_spawn=False,
                ),
                poll_inbox=True,
            )
            if result:
                self.bus.send(
                    sender=name,
                    to="lead",
                    content=result,
                    msg_type="status",
                    extra={"status": "completed"},
                )
        finally:
            self.set_status(name, "idle")
            print(f"[team] {name} is now idle")

    def join_all(self, timeout: float | None = None) -> None:
        for thread in list(self.threads.values()):
            thread.join(timeout=timeout)


BUS: MessageBus | None = None
MANAGER: TeammateManager | None = None


def _init_globals() -> None:
    global BUS, MANAGER
    if BUS is None:
        BUS = MessageBus(TEAM_DIR)
        MANAGER = TeammateManager(TEAM_DIR, bus=BUS)


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


def build_tools(can_spawn: bool = True) -> list[dict[str, Any]]:
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
    ]

    if can_spawn:
        tools.append(
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
            }
        )

    tools.extend(
        [
            {
                "name": "send_message",
                "description": "Send a message to one teammate or the lead.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                        "msg_type": {"type": "string"},
                    },
                    "required": ["to", "content"],
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
                "name": "list_team",
                "description": "List current team members and their status.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
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
    )

    return tools


def make_tool_handlers(
    state: AgentState,
    manager: TeammateManager,
    bus: MessageBus,
    can_spawn: bool = True,
) -> dict[str, Any]:
    def spawn_teammate(**kw: Any) -> str:
        if not can_spawn or state.name != "lead":
            return "Error: only the lead can spawn teammates"
        return manager.spawn(kw["name"], kw["role"], kw["prompt"])

    def send_message(**kw: Any) -> str:
        msg_type = kw.get("msg_type", "message")
        message = bus.send(state.name, kw["to"], kw["content"], msg_type=msg_type)
        return (
            f"Sent {message['type']} from {message['from']} "
            f"to {message['to']}"
        )

    def broadcast_message(**kw: Any) -> str:
        recipients = [member["name"] for member in manager.list_members()]
        delivered = bus.broadcast(state.name, recipients, kw["content"])
        return f"Broadcast delivered to {delivered} teammates"

    def read_inbox(**kw: Any) -> str:
        target = kw.get("name") or state.name
        if state.name != "lead" and target != state.name:
            return f"Error: {state.name} can only read its own inbox"
        messages = bus.drain(target)
        return MessageBus.format_messages(messages)

    def list_team(**_: Any) -> str:
        return manager.describe_team()

    def end_turn(**kw: Any) -> str:
        return kw["response"]

    handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(
            kw["path"], kw["old_text"], kw["new_text"]
        ),
        "send_message": send_message,
        "broadcast_message": broadcast_message,
        "read_inbox": read_inbox,
        "list_team": list_team,
        "end_turn": end_turn,
    }
    if can_spawn:
        handlers["spawn_teammate"] = spawn_teammate
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
                output = (
                    handler(**block.input)
                    if handler
                    else f"Unknown tool: {block.name}"
                )
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

        state.add_message("user", results)

    return last_response


def run_lead_turn(task: str, state: AgentState | None = None) -> tuple[str, AgentState]:
    _init_globals()
    state = state or AgentState(name="lead", role="team lead")
    state.add_message("user", task)
    result = agent_loop(
        state=state,
        system=LEAD_SYSTEM,
        tools=build_tools(can_spawn=True),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=MANAGER,
            bus=BUS,
            can_spawn=True,
        ),
        poll_inbox=True,
        bus=BUS,
    )
    return result, state


def main() -> None:
    _init_globals()
    lead_state = AgentState(name="lead", role="team lead")

    while True:
        try:
            query = input("\033[36ms09 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip() == "/team":
            print(MANAGER.describe_team())
            print()
            continue
        if query.strip() == "/inbox":
            inbox_messages = BUS.drain("lead")
            print(MessageBus.format_messages(inbox_messages))
            print()
            continue

        result, lead_state = run_lead_turn(query, state=lead_state)
        if result:
            print(result)
        print()


if __name__ == "__main__":
    main()
