from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agents.s10_team_protocols import (
    AgentState,
    MessageBus,
    ProtocolRegistry,
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


def test_protocol_registry_tracks_shutdown_lifecycle() -> None:
    protocols = ProtocolRegistry()

    request_id = protocols.create_shutdown_request("alice")
    pending = protocols.get_shutdown_request(request_id)
    resolved = protocols.resolve_shutdown_request(request_id, approve=True, reason="done")

    assert pending is not None
    assert pending["target"] == "alice"
    assert pending["status"] == "pending"
    assert resolved is not None
    assert resolved["status"] == "approved"
    assert resolved["reason"] == "done"


def test_protocol_registry_tracks_plan_lifecycle() -> None:
    protocols = ProtocolRegistry()

    request_id = protocols.create_plan_request("bob", "Refactor auth in two steps")
    pending = protocols.get_plan_request(request_id)
    resolved = protocols.resolve_plan_request(
        request_id,
        approve=False,
        feedback="too risky",
    )

    assert pending is not None
    assert pending["from"] == "bob"
    assert pending["status"] == "pending"
    assert resolved is not None
    assert resolved["status"] == "rejected"
    assert resolved["feedback"] == "too risky"


def test_lead_shutdown_request_handler_sends_message_and_tracks_pending(
    tmp_path: Path,
) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols)
    state = AgentState(name="lead", role="team lead")

    handlers = make_tool_handlers(
        state=state,
        manager=manager,
        bus=bus,
        protocols=protocols,
        role="lead",
    )

    result = handlers["shutdown_request"](teammate="alice")
    request_id = next(iter(protocols.shutdown_requests))
    inbox = bus.drain("alice")

    assert request_id in result
    assert protocols.shutdown_requests[request_id]["status"] == "pending"
    assert inbox[0]["type"] == "shutdown_request"
    assert inbox[0]["request_id"] == request_id


def test_teammate_shutdown_response_marks_exit_and_updates_tracker(
    tmp_path: Path,
) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols)
    state = AgentState(name="alice", role="coder")

    request_id = protocols.create_shutdown_request("alice")
    bus.send(
        "lead",
        "alice",
        "Please shut down gracefully.",
        "shutdown_request",
        {"request_id": request_id},
    )

    handlers = make_tool_handlers(
        state=state,
        manager=manager,
        bus=bus,
        protocols=protocols,
        role="teammate",
    )

    result = handlers["shutdown_response"](
        request_id=request_id,
        approve=True,
        reason="all clean",
    )
    lead_inbox = bus.drain("lead")

    assert result == "Shutdown approved"
    assert state.should_exit is True
    assert protocols.shutdown_requests[request_id]["status"] == "approved"
    assert lead_inbox[0]["type"] == "shutdown_response"
    assert lead_inbox[0]["approve"] is True


def test_teammate_submit_plan_and_lead_review_form_request_response_pair(
    tmp_path: Path,
) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols)
    lead_state = AgentState(name="lead", role="team lead")
    bob_state = AgentState(name="bob", role="coder")

    bob_handlers = make_tool_handlers(
        state=bob_state,
        manager=manager,
        bus=bus,
        protocols=protocols,
        role="teammate",
    )
    lead_handlers = make_tool_handlers(
        state=lead_state,
        manager=manager,
        bus=bus,
        protocols=protocols,
        role="lead",
    )

    submit_result = bob_handlers["submit_plan"](plan="Refactor auth in two safe steps")
    request_id = next(iter(protocols.plan_requests))
    lead_inbox = bus.drain("lead")
    review_result = lead_handlers["review_plan"](
        request_id=request_id,
        approve=False,
        feedback="Break this down further first",
    )
    bob_inbox = bus.drain("bob")

    assert request_id in submit_result
    assert lead_inbox[0]["type"] == "plan_approval_request"
    assert lead_inbox[0]["request_id"] == request_id
    assert review_result == "Plan rejected for 'bob'"
    assert protocols.plan_requests[request_id]["status"] == "rejected"
    assert bob_inbox[0]["type"] == "plan_approval_response"
    assert bob_inbox[0]["approve"] is False


def test_agent_loop_injects_protocol_inbox_before_model_call(
    tmp_path: Path, monkeypatch
) -> None:
    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols)
    state = AgentState(name="lead", role="team lead")
    state.add_message("user", "check protocol updates")
    bus.send(
        "bob",
        "lead",
        "Plan submitted",
        "plan_approval_request",
        {"request_id": "req1234", "plan": "Refactor auth safely"},
    )

    def fake_create(**kwargs):
        messages = kwargs["messages"]
        assert any(
            msg["role"] == "user" and "<inbox>" in str(msg["content"])
            for msg in messages
        )
        return _make_response("end_turn", [_make_text_block("saw protocol inbox")])

    monkeypatch.setattr("agents.s10_team_protocols.client.messages.create", fake_create)

    result = agent_loop(
        state=state,
        system="lead system",
        tools=build_tools(role="lead"),
        tool_handlers=make_tool_handlers(
            state=state,
            manager=manager,
            bus=bus,
            protocols=protocols,
            role="lead",
        ),
        poll_inbox=True,
        bus=bus,
    )

    assert result == "saw protocol inbox"


def test_protocol_flow_plan_then_shutdown_end_to_end(tmp_path: Path, monkeypatch) -> None:
    import agents.s10_team_protocols as mod

    bus = MessageBus(tmp_path / ".team")
    protocols = ProtocolRegistry()
    manager = TeammateManager(tmp_path / ".team", bus=bus, protocols=protocols)
    monkeypatch.setattr(mod, "WORKDIR", tmp_path)

    def fake_create(**kwargs):
        system = kwargs["system"]
        text = _messages_to_text(kwargs["messages"])

        if "team lead" in system:
            if "Spawned teammate 'bob'" not in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "spawn_teammate",
                            "lead-spawn-bob",
                            {
                                "name": "bob",
                                "role": "coder",
                                "prompt": "Prepare a plan before touching auth logic.",
                            },
                        )
                    ],
                )

            if '"type": "plan_approval_request"' in text and not any(
                request["status"] == "approved"
                for request in protocols.plan_requests.values()
            ):
                request_id = next(iter(protocols.plan_requests))
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "review_plan",
                            "lead-approve-plan",
                            {
                                "request_id": request_id,
                                "approve": True,
                                "feedback": "Approved. Proceed carefully.",
                            },
                        )
                    ],
                )

            if "Bob completed the approved auth plan." in text and not protocols.shutdown_requests:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "shutdown_request",
                            "lead-shutdown",
                            {"teammate": "bob"},
                        )
                    ],
                )

            if '"type": "shutdown_response"' in text:
                request_id = next(iter(protocols.shutdown_requests))
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "check_shutdown",
                            "lead-check-shutdown",
                            {"request_id": request_id},
                        ),
                        _make_tool_use_block(
                            "end_turn",
                            "lead-end",
                            {"response": "protocol flow complete"},
                        ),
                    ],
                )

            return _make_response(
                "tool_use",
                [_make_tool_use_block("list_team", "lead-wait", {})],
            )

        if "Your teammate name is bob." in system:
            if '"type": "shutdown_request"' in text:
                request_id = next(iter(protocols.shutdown_requests))
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "shutdown_response",
                            "bob-shutdown-response",
                            {
                                "request_id": request_id,
                                "approve": True,
                                "reason": "cleanup complete",
                            },
                        )
                    ],
                )

            if '"type": "plan_approval_response"' in text and '"approve": true' in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "send_message",
                            "bob-status",
                            {
                                "to": "lead",
                                "content": "Bob completed the approved auth plan.",
                                "msg_type": "status",
                            },
                        ),
                        _make_tool_use_block(
                            "end_turn",
                            "bob-end",
                            {"response": "bob finished approved work"},
                        ),
                    ],
                )

            if "Plan submitted (request_id=" not in text:
                return _make_response(
                    "tool_use",
                    [
                        _make_tool_use_block(
                            "submit_plan",
                            "bob-submit-plan",
                            {"plan": "1. inspect auth module\n2. refactor safely\n3. run checks"},
                        )
                    ],
                )

            return _make_response(
                "tool_use",
                [_make_tool_use_block("read_inbox", "bob-read", {})],
            )

        raise AssertionError(f"Unexpected system prompt: {system}")

    monkeypatch.setattr("agents.s10_team_protocols.client.messages.create", fake_create)

    lead_state = AgentState(name="lead", role="team lead")
    lead_state.add_message(
        "user",
        "Spawn bob, approve his auth refactor plan, then gracefully shut him down.",
    )

    result = agent_loop(
        state=lead_state,
        system=mod.LEAD_SYSTEM,
        tools=build_tools(role="lead"),
        tool_handlers=make_tool_handlers(
            state=lead_state,
            manager=manager,
            bus=bus,
            protocols=protocols,
            role="lead",
        ),
        poll_inbox=True,
        bus=bus,
    )
    manager.join_all(timeout=5)

    transcript = _messages_to_text(lead_state.messages)
    request_id = next(iter(protocols.shutdown_requests))

    assert result == "protocol flow complete"
    assert protocols.plan_requests[next(iter(protocols.plan_requests))]["status"] == "approved"
    assert protocols.shutdown_requests[request_id]["status"] == "approved"
    assert "Bob completed the approved auth plan." in transcript
    roster = {member["name"]: member["status"] for member in manager.list_members()}
    assert roster["bob"] == "shutdown"
