from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agents.s09_agent_teams import (
    AgentState,
    MessageBus,
    TeammateManager,
    agent_loop,
    build_tools,
    make_tool_handlers,
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


def test_message_bus_send_and_drain(tmp_path: Path) -> None:
    bus = MessageBus(tmp_path / ".team")

    bus.send("lead", "alice", "hello")
    bus.send("bob", "alice", "hi", msg_type="status")

    inbox = bus.drain("alice")

    assert len(inbox) == 2
    assert inbox[0]["from"] == "lead"
    assert inbox[1]["type"] == "status"
    assert bus.drain("alice") == []


def test_teammate_manager_spawn_persists_member_config(tmp_path: Path, monkeypatch) -> None:
    bus = MessageBus(tmp_path / ".team")
    manager = TeammateManager(tmp_path / ".team", bus=bus)

    def fake_loop(self, name: str, role: str, prompt: str) -> None:
        self.bus.send(name, "lead", f"finished: {prompt}", msg_type="status")
        self.set_status(name, "idle")

    monkeypatch.setattr(TeammateManager, "_teammate_loop", fake_loop)

    result = manager.spawn("alice", "coder", "review requirements")
    manager.join_all(timeout=5)

    config = json.loads((tmp_path / ".team" / "config.json").read_text())
    alice = next(member for member in config["members"] if member["name"] == "alice")

    assert "Spawned teammate 'alice'" in result
    assert alice["role"] == "coder"
    assert alice["status"] == "idle"
    lead_inbox = bus.drain("lead")
    assert lead_inbox[0]["content"] == "finished: review requirements"


def test_agent_loop_injects_inbox_before_model_call(tmp_path: Path, monkeypatch) -> None:
    bus = MessageBus(tmp_path / ".team")
    manager = TeammateManager(tmp_path / ".team", bus=bus)
    state = AgentState(name="lead", role="team lead")
    state.add_message("user", "check updates")
    bus.send("alice", "lead", "phase 1 complete", msg_type="status")

    def fake_create(**kwargs):
        messages = kwargs["messages"]
        assert any(
            msg["role"] == "user" and "<inbox>" in str(msg["content"])
            for msg in messages
        )
        return _make_response("end_turn", [_make_text_block("saw inbox")])

    monkeypatch.setattr("agents.s09_agent_teams.client.messages.create", fake_create)

    result = agent_loop(
        state=state,
        system="lead system",
        tools=build_tools(can_spawn=True),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=manager,
            bus=bus,
            can_spawn=True,
        ),
        poll_inbox=True,
        bus=bus,
    )

    assert result == "saw inbox"


def test_agent_loop_handles_spawn_and_end_turn(tmp_path: Path, monkeypatch) -> None:
    bus = MessageBus(tmp_path / ".team")
    manager = TeammateManager(tmp_path / ".team", bus=bus)
    state = AgentState(name="lead", role="team lead")
    state.add_message("user", "start team")

    def fake_spawn(name: str, role: str, prompt: str) -> str:
        manager.ensure_member(name, role, status="working")
        return f"Spawned teammate '{name}' ({role})"

    monkeypatch.setattr(manager, "spawn", fake_spawn)

    responses = [
        _make_response(
            "tool_use",
            [
                _make_tool_use_block(
                    "spawn_teammate",
                    "tool-1",
                    {"name": "alice", "role": "coder", "prompt": "check tests"},
                )
            ],
        ),
        _make_response(
            "tool_use",
            [
                _make_tool_use_block(
                    "end_turn",
                    "tool-2",
                    {"response": "alice has been assigned"},
                )
            ],
        ),
    ]

    def fake_create(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("agents.s09_agent_teams.client.messages.create", fake_create)

    result = agent_loop(
        state=state,
        system="lead system",
        tools=build_tools(can_spawn=True),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=manager,
            bus=bus,
            can_spawn=True,
        ),
        poll_inbox=False,
        bus=bus,
    )

    assert result == "alice has been assigned"
    assert any(member["name"] == "alice" for member in manager.list_members())


def test_read_inbox_tool_restricts_non_lead_access(tmp_path: Path) -> None:
    bus = MessageBus(tmp_path / ".team")
    manager = TeammateManager(tmp_path / ".team", bus=bus)
    state = AgentState(name="alice", role="coder")

    handlers = make_tool_handlers(
        state=state,
        manager=manager,
        bus=bus,
        can_spawn=False,
    )

    result = handlers["read_inbox"](name="bob")

    assert result == "Error: alice can only read its own inbox"


def test_team_of_three_collaborates_to_write_release_note(
    tmp_path: Path, monkeypatch
) -> None:
    import agents.s09_agent_teams as mod

    bus = MessageBus(tmp_path / ".team")
    manager = TeammateManager(tmp_path / ".team", bus=bus)
    monkeypatch.setattr(mod, "WORKDIR", tmp_path)

    release_note = (
        "# Atlas Release Note\n\n"
        "Atlas now ships with faster sync, safer retries, and clearer logs.\n\n"
        "## Why it matters\n"
        "- Teams ship updates with less manual checking.\n"
        "- Retry behavior is easier to reason about during incidents.\n"
        "- Logs are easier to scan during debugging.\n"
    )

    def fake_create(**kwargs):
        system = kwargs["system"]
        text = _messages_to_text(kwargs["messages"])

        if "team lead" in system:
            if "Polished release note:" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "write_file",
                            "lead-write",
                            {
                                "path": "team_release_note.md",
                                "content": release_note,
                            },
                        ),
                        _make_tool_use_block(
                            "end_turn",
                            "lead-end",
                            {"response": "release note completed"},
                        ),
                    ],
                )

            if "Spawned teammate 'alice'" not in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "spawn_teammate",
                            "lead-spawn-alice",
                            {
                                "name": "alice",
                                "role": "researcher",
                                "prompt": "Gather three launch facts and send them to bob.",
                            },
                        ),
                        _make_tool_use_block(
                            "spawn_teammate",
                            "lead-spawn-bob",
                            {
                                "name": "bob",
                                "role": "writer",
                                "prompt": "Wait for research, then draft a release note and send it to carol.",
                            },
                        ),
                        _make_tool_use_block(
                            "spawn_teammate",
                            "lead-spawn-carol",
                            {
                                "name": "carol",
                                "role": "editor",
                                "prompt": "Wait for bob's draft, polish it, and send the final version to lead.",
                            },
                        ),
                    ],
                )

            return _make_response(
                "tool_use",
                [_make_tool_use_block("list_team", f"lead-wait-{len(text)}", {})],
            )

        if "Your teammate name is alice." in system:
            if "Sent message from alice to bob" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "end_turn",
                            "alice-end",
                            {"response": "alice finished research"},
                        )
                    ],
                )

            return _make_response(
                "tool_use",
                [
                    _make_tool_use_block(
                        "send_message",
                        "alice-to-bob",
                        {
                            "to": "bob",
                            "content": (
                                "Research brief: Atlas adds faster sync, safer retries, "
                                "and clearer logs."
                            ),
                            "msg_type": "status",
                        },
                    ),
                    _make_tool_use_block(
                        "send_message",
                        "alice-to-lead",
                        {
                            "to": "lead",
                            "content": "Alice researched the release facts and sent them to bob.",
                            "msg_type": "status",
                        },
                    ),
                ],
            )

        if "Your teammate name is bob." in system:
            if "Sent message from bob to carol" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "end_turn",
                            "bob-end",
                            {"response": "bob finished draft"},
                        )
                    ],
                )

            if "Research brief:" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "send_message",
                            "bob-to-carol",
                            {
                                "to": "carol",
                                "content": (
                                    "Draft release note: Atlas helps teams ship faster with faster sync, "
                                    "safer retries, and clearer logs."
                                ),
                                "msg_type": "draft",
                            },
                        ),
                        _make_tool_use_block(
                            "send_message",
                            "bob-to-lead",
                            {
                                "to": "lead",
                                "content": "Bob drafted the release note and handed it to carol.",
                                "msg_type": "status",
                            },
                        ),
                    ],
                )

            return _make_response(
                "tool_use",
                [_make_tool_use_block("read_inbox", "bob-read", {})],
            )

        if "Your teammate name is carol." in system:
            if "Sent status from carol to lead" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "end_turn",
                            "carol-end",
                            {"response": "carol polished the draft"},
                        )
                    ],
                )

            if "Draft release note:" in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "send_message",
                            "carol-to-lead",
                            {
                                "to": "lead",
                                "content": f"Polished release note:\n{release_note}",
                                "msg_type": "status",
                            },
                        )
                    ],
                )

            return _make_response(
                "tool_use",
                [_make_tool_use_block("read_inbox", "carol-read", {})],
            )

        raise AssertionError(f"Unexpected system prompt: {system}")

    monkeypatch.setattr("agents.s09_agent_teams.client.messages.create", fake_create)

    lead_state = AgentState(name="lead", role="team lead")
    lead_state.add_message(
        "user",
        "Coordinate three teammates to write a release note for Atlas.",
    )

    result = agent_loop(
        state=lead_state,
        system=mod.LEAD_SYSTEM,
        tools=build_tools(can_spawn=True),
        tool_handlers=make_tool_handlers(
            state=lead_state,
            manager=manager,
            bus=bus,
            can_spawn=True,
        ),
        poll_inbox=True,
        bus=bus,
    )
    manager.join_all(timeout=5)

    note_path = tmp_path / "team_release_note.md"
    assert result == "release note completed"
    assert note_path.exists()
    assert note_path.read_text(encoding="utf-8") == release_note

    transcript = _messages_to_text(lead_state.messages)
    assert "Alice researched the release facts" in transcript
    assert "Bob drafted the release note" in transcript
    assert "Polished release note:" in transcript

    roster = {member["name"]: member["status"] for member in manager.list_members()}
    assert roster["alice"] == "idle"
    assert roster["bob"] == "idle"
    assert roster["carol"] == "idle"
