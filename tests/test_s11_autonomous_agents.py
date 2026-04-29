from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agents.s11_autonomous_agents import (
    AgentState,
    MessageBus,
    ProtocolRegistry,
    TaskBoard,
    TeammateManager,
    agent_loop,
    build_tools,
    make_identity_block,
    make_tool_handlers,
    reinject_identity_if_needed,
)


def _messages_to_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(json.dumps(item, ensure_ascii=True))
                elif hasattr(item, "text"):
                    parts.append(str(item.text))
                elif hasattr(item, "name"):
                    parts.append(str(item.name))
                else:
                    parts.append(repr(item))
        else:
            try:
                parts.append(json.dumps(content, ensure_ascii=True))
            except TypeError:
                parts.append(repr(content))
    return "\n".join(parts)


def _make_text_block(text: str = "done") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(name: str, tool_id: str, tool_input: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = tool_input
    return block


def _make_response(stop_reason: str, content: list[MagicMock]) -> MagicMock:
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = content
    return response


def _write_task(
    tasks_dir: Path,
    task_id: str,
    title: str,
    status: str = "pending",
    owner: str | None = None,
    dependencies: list[str] | None = None,
) -> None:
    task = {
        "id": task_id,
        "title": title,
        "status": status,
        "owner": owner,
        "dependencies": dependencies or [],
    }
    (tasks_dir / f"task_{task_id}.json").write_text(json.dumps(task, indent=2))


def test_task_board_scan_only_returns_ready_pending_unowned_tasks(tmp_path: Path) -> None:
    tasks = TaskBoard(tmp_path / ".tasks")
    _write_task(tasks.dir, "t1", "ready")
    _write_task(tasks.dir, "t2", "owned", owner="alice")
    _write_task(tasks.dir, "t3", "done", status="completed")
    _write_task(tasks.dir, "t4", "blocked", dependencies=["t1"])

    ready = tasks.scan_unclaimed_tasks()

    assert [task["id"] for task in ready] == ["t1"]

    tasks.update_task_status("t1", "completed")
    ready_after = tasks.scan_unclaimed_tasks()

    assert [task["id"] for task in ready_after] == ["t4"]


def test_claim_task_marks_owner_and_in_progress(tmp_path: Path) -> None:
    tasks = TaskBoard(tmp_path / ".tasks")
    _write_task(tasks.dir, "t1", "write tests")

    result = tasks.claim_task("t1", "bob")
    task = tasks.get_task("t1")

    assert result == "Claimed task #t1 for bob"
    assert task is not None
    assert task["raw"]["owner"] == "bob"
    assert task["raw"]["status"] == "in_progress"


def test_identity_reinjection_prepends_identity_when_context_is_short() -> None:
    state = AgentState(name="alice", role="coder")
    state.add_message("user", "short context")

    reinject_identity_if_needed(state, team_name="autonomy-lab")

    assert state.messages[0] == make_identity_block("alice", "coder", "autonomy-lab")
    assert state.messages[1]["role"] == "assistant"
    assert "I am alice" in state.messages[1]["content"]


def test_idle_poll_claims_task_and_injects_identity(tmp_path: Path) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    tasks = TaskBoard(tmp_path / ".tasks")
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols, tasks=tasks)
    state = AgentState(name="bob", role="coder")
    state.add_message("user", "idle now")
    _write_task(tasks.dir, "t1", "Autonomous task")

    resumed = manager._idle_poll(
        state,
        role="coder",
        team_name="default",
        poll_interval=0,
        idle_timeout=0,
    )
    task = tasks.get_task("t1")
    transcript = _messages_to_text(state.messages)

    assert resumed is True
    assert task is not None
    assert task["raw"]["owner"] == "bob"
    assert state.messages[0]["content"].startswith("<identity>")
    assert "<auto-claimed>Task #t1: Autonomous task" in transcript


def test_agent_loop_injects_inbox_before_model_call(tmp_path: Path, monkeypatch) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    tasks = TaskBoard(tmp_path / ".tasks")
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols, tasks=tasks)
    state = AgentState(name="lead", role="team lead")
    state.add_message("user", "check updates")
    bus.send("alice", "lead", "claimed task", msg_type="status")

    def fake_create(**kwargs):
        messages = kwargs["messages"]
        assert any(
            msg["role"] == "user" and "<inbox>" in str(msg["content"])
            for msg in messages
        )
        return _make_response("end_turn", [_make_text_block("saw autonomous inbox")])

    monkeypatch.setattr("agents.s11_autonomous_agents.client.messages.create", fake_create)

    result, stop_tool = agent_loop(
        state=state,
        system="lead system",
        tools=build_tools(role="lead"),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=manager,
            bus=bus,
            protocols=protocols,
            tasks=tasks,
            role="lead",
        ),
        poll_inbox=True,
        bus=bus,
        stop_tools={"end_turn"},
    )

    assert result == "saw autonomous inbox"
    assert stop_tool is None


def test_autonomous_teammate_auto_claims_task_then_shuts_down(
    tmp_path: Path, monkeypatch
) -> None:
    import agents.s11_autonomous_agents as mod

    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    tasks = TaskBoard(tmp_path / ".tasks")
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols, tasks=tasks)
    _write_task(tasks.dir, "t1", "Write release summary")

    monkeypatch.setattr(mod, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(mod, "IDLE_TIMEOUT", 0.02)

    def fake_create(**kwargs):
        system = kwargs["system"]
        text = _messages_to_text(kwargs["messages"])
        assert "Your teammate name is bob." in system

        if "<auto-claimed>Task #t1: Write release summary" in text:
            return _make_response(
                "tool_use",
                [
                    _make_tool_use_block(
                        "send_message",
                        "bob-status",
                        {
                            "to": "lead",
                            "content": "bob finished task t1",
                            "msg_type": "status",
                        },
                    ),
                    _make_tool_use_block(
                        "update_task_status",
                        "bob-done",
                        {"task_id": "t1", "status": "completed"},
                    ),
                    _make_tool_use_block("idle", "bob-idle-again", {}),
                ],
            )

        return _make_response(
            "tool_use",
            [_make_tool_use_block("idle", "bob-idle-first", {})],
        )

    monkeypatch.setattr("agents.s11_autonomous_agents.client.messages.create", fake_create)

    result = manager.spawn("bob", "coder", "Look for work from the board.")
    manager.join_all(timeout=1)

    lead_inbox = bus.drain("lead")
    task = tasks.get_task("t1")
    roster = {member["name"]: member["status"] for member in manager.list_members()}

    assert "Spawned teammate 'bob'" in result
    assert lead_inbox[0]["content"] == "bob finished task t1"
    assert task is not None
    assert task["raw"]["status"] == "completed"
    assert task["raw"]["owner"] == "bob"
    assert roster["bob"] == "shutdown"
