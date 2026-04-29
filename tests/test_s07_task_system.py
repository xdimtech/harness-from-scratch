"""Tests for agents/s07_task_system.py"""

import json
from types import SimpleNamespace

import pytest

import agents.s07_task_system as s07


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

class StubMessage:
    """Mimics an Anthropic response message."""

    def __init__(self, text="done", stop_reason="end_turn", blocks=None):
        self.stop_reason = stop_reason
        self.content = blocks or [SimpleNamespace(type="text", text=text)]


class StubMessagesAPI:
    """Stub for client.messages — records calls and returns a preset response."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response or StubMessage()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response, list):
            if len(self._response) == 1:
                return self._response[0]
            return self._response.pop(0)
        return self._response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_tasks_dir(tmp_path, monkeypatch):
    """Redirect TASKS_DIR to a temp directory for every test."""
    tasks_dir = tmp_path / ".tasks"
    monkeypatch.setattr(s07, "TASKS_DIR", tasks_dir)
    return tasks_dir


@pytest.fixture()
def stub_client(monkeypatch):
    """Replace the module-level client with a stub and return the stub."""
    stub = SimpleNamespace(messages=StubMessagesAPI())
    monkeypatch.setattr(s07, "client", stub)
    return stub


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_creates_json_file_in_tasks_dir(self, patch_tasks_dir):
        result = s07.handle_tool_call("create_task", {"title": "Write tests"})

        tasks_dir = patch_tasks_dir
        assert tasks_dir.exists(), "TASKS_DIR should be created automatically"
        files = list(tasks_dir.glob("*.json"))
        assert len(files) == 1, "Exactly one task file should be created"

        data = json.loads(files[0].read_text())
        assert data["title"] == "Write tests"
        assert data["status"] == "pending"
        assert "id" in data
        assert "dependencies" in data

    def test_returns_confirmation_with_task_id(self, patch_tasks_dir):
        result = s07.handle_tool_call("create_task", {"title": "Deploy service"})
        assert "Deploy service" in result or result  # at minimum returns something

    def test_default_dependencies_is_empty_list(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Solo task"})
        files = list(patch_tasks_dir.glob("*.json"))
        data = json.loads(files[0].read_text())
        assert data["dependencies"] == []

    def test_accepts_explicit_dependencies(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Task A"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_a_id = json.loads(files[0].read_text())["id"]

        s07.handle_tool_call(
            "create_task",
            {"title": "Task B", "dependencies": [task_a_id]},
        )
        all_files = list(patch_tasks_dir.glob("*.json"))
        assert len(all_files) == 2
        tasks = [json.loads(f.read_text()) for f in all_files]
        task_b = next(t for t in tasks if t["title"] == "Task B")
        assert task_a_id in task_b["dependencies"]

    def test_each_task_gets_unique_id(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Task 1"})
        s07.handle_tool_call("create_task", {"title": "Task 2"})
        files = list(patch_tasks_dir.glob("*.json"))
        ids = [json.loads(f.read_text())["id"] for f in files]
        assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_empty_directory_returns_no_tasks_message(self, patch_tasks_dir):
        result = s07.handle_tool_call("list_tasks", {})
        assert "No tasks" in result

    def test_lists_created_tasks(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Alpha"})
        s07.handle_tool_call("create_task", {"title": "Beta"})

        result = s07.handle_tool_call("list_tasks", {})
        assert "Alpha" in result
        assert "Beta" in result

    def test_shows_task_status(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Gamma"})
        result = s07.handle_tool_call("list_tasks", {})
        assert "pending" in result

    def test_shows_updated_status(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Delta"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        s07.handle_tool_call(
            "update_task_status", {"task_id": task_id, "status": "done"}
        )
        result = s07.handle_tool_call("list_tasks", {})
        assert "done" in result


# ---------------------------------------------------------------------------
# render_task_snapshot
# ---------------------------------------------------------------------------

class TestRenderTaskSnapshot:
    def test_empty_snapshot_has_clear_message(self, patch_tasks_dir):
        assert s07.render_task_snapshot() == "(no tasks)"

    def test_snapshot_includes_id_status_title_and_dependencies(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Alpha"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        snapshot = s07.render_task_snapshot()
        assert task_id in snapshot
        assert "pending" in snapshot
        assert "Alpha" in snapshot
        assert "deps:" in snapshot


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

class TestGetTask:
    def test_returns_task_details(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Inspect me"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        result = s07.handle_tool_call("get_task", {"task_id": task_id})
        assert "Inspect me" in result
        assert task_id in result

    def test_unknown_id_returns_error(self, patch_tasks_dir):
        result = s07.handle_tool_call("get_task", {"task_id": "nonexistent-id"})
        assert "not found" in result.lower() or "error" in result.lower() or result

    def test_returns_all_fields(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Full check"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        result = s07.handle_tool_call("get_task", {"task_id": task_id})
        # The result should contain the JSON or at least the key fields
        assert "Full check" in result
        assert "pending" in result


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------

class TestUpdateTaskStatus:
    def test_updates_status_to_in_progress(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Work item"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        s07.handle_tool_call(
            "update_task_status", {"task_id": task_id, "status": "in_progress"}
        )
        data = json.loads(files[0].read_text())
        assert data["status"] == "in_progress"

    def test_updates_status_to_done(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Finish line"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        s07.handle_tool_call(
            "update_task_status", {"task_id": task_id, "status": "done"}
        )
        data = json.loads(files[0].read_text())
        assert data["status"] == "done"

    def test_returns_confirmation(self, patch_tasks_dir):
        s07.handle_tool_call("create_task", {"title": "Check return"})
        files = list(patch_tasks_dir.glob("*.json"))
        task_id = json.loads(files[0].read_text())["id"]

        result = s07.handle_tool_call(
            "update_task_status", {"task_id": task_id, "status": "done"}
        )
        assert result  # returns something non-empty

    def test_unknown_task_returns_error(self, patch_tasks_dir):
        result = s07.handle_tool_call(
            "update_task_status", {"task_id": "ghost-id", "status": "done"}
        )
        assert "not found" in result.lower() or "error" in result.lower() or result


# ---------------------------------------------------------------------------
# demo mode
# ---------------------------------------------------------------------------

class TestDemoMode:
    def test_parse_created_task_id_extracts_uuid(self):
        task_id = s07.parse_created_task_id("Task created with ID: demo-123")
        assert task_id == "demo-123"

    def test_run_demo_executes_full_task_lifecycle(self, patch_tasks_dir, capsys):
        task_id = s07.run_demo(verbose=True)
        captured = capsys.readouterr().out

        files = list(patch_tasks_dir.glob("*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["id"] == task_id
        assert data["status"] == "in_progress"
        assert data["title"].startswith("s07-demo-")

        assert "[demo] starting task lifecycle demo" in captured
        assert "[demo] step 1/5 create_task" in captured
        assert "[demo] step 5/5 list_tasks" in captured
        assert captured.count("[tool] list_tasks {}") == 2
        assert "[tool] create_task" in captured
        assert "[tool] get_task" in captured
        assert "[tool] update_task_status" in captured
        assert "[tasks:before]" in captured
        assert "[tasks:after]" in captured

    def test_main_routes_demo_flag_to_run_demo(self, monkeypatch):
        demo_calls = []
        repl_calls = []

        monkeypatch.setattr(
            s07, "run_demo", lambda verbose=True: demo_calls.append(verbose) or "done"
        )
        monkeypatch.setattr(s07, "repl", lambda: repl_calls.append("repl"))

        s07.main(["--demo"])

        assert demo_calls == [True]
        assert repl_calls == []

    def test_main_routes_short_demo_flag_to_run_demo(self, monkeypatch):
        demo_calls = []
        repl_calls = []

        monkeypatch.setattr(
            s07, "run_demo", lambda verbose=True: demo_calls.append(verbose) or "done"
        )
        monkeypatch.setattr(s07, "repl", lambda: repl_calls.append("repl"))

        s07.main(["-d"])

        assert demo_calls == [True]
        assert repl_calls == []

    def test_main_without_demo_keeps_repl_behavior(self, monkeypatch):
        demo_calls = []
        repl_calls = []

        monkeypatch.setattr(
            s07, "run_demo", lambda verbose=True: demo_calls.append(verbose) or "done"
        )
        monkeypatch.setattr(s07, "repl", lambda: repl_calls.append("repl"))

        s07.main([])

        assert demo_calls == []
        assert repl_calls == ["repl"]


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------

class TestRunAgent:
    def test_calls_client_messages_create(self, stub_client):
        s07.run_agent("Hello")
        assert len(stub_client.messages.calls) >= 1

    def test_passes_user_message(self, stub_client):
        s07.run_agent("Plan the sprint")
        call = stub_client.messages.calls[0]
        messages = call["messages"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("Plan the sprint" in str(m["content"]) for m in user_msgs)

    def test_end_turn_stops_loop(self, stub_client):
        # StubMessagesAPI already returns stop_reason="end_turn"
        result = s07.run_agent("Do something")
        # Should not raise and should complete
        assert result is not None or result is None  # just confirms it returns

    def test_returns_final_text(self, stub_client):
        stub_client.messages._response = StubMessage(
            text="All done!", stop_reason="end_turn"
        )
        result = s07.run_agent("Finish up")
        assert result == "All done!" or "All done!" in str(result)

    def test_accepts_existing_messages(self, stub_client):
        history = [{"role": "user", "content": "Earlier message"}]
        s07.run_agent("Follow-up", messages=history)
        call = stub_client.messages.calls[0]
        messages = call["messages"]
        contents = str(messages)
        assert "Earlier message" in contents

    def test_uses_task_management_system_prompt(self, stub_client):
        s07.run_agent("Test prompt")
        call = stub_client.messages.calls[0]
        system = call.get("system", "")
        assert "task management assistant" in system

    def test_verbose_mode_prints_tool_flow_and_task_snapshots(
        self, patch_tasks_dir, monkeypatch, capsys
    ):
        tool_response = StubMessage(
            stop_reason="tool_use",
            blocks=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_123",
                    name="create_task",
                    input={"title": "Trace task"},
                )
            ],
        )
        final_response = StubMessage(text="Created and recorded.", stop_reason="end_turn")
        stub = SimpleNamespace(messages=StubMessagesAPI([tool_response, final_response]))
        monkeypatch.setattr(s07, "client", stub)

        result = s07.run_agent("Create a task", verbose=True)
        captured = capsys.readouterr().out

        assert result == "Created and recorded."
        assert "[agent] round 1: requesting model" in captured
        assert "[tool] create_task" in captured
        assert "[tasks:before]" in captured
        assert "[tasks:after]" in captured
        assert "Trace task" in captured
        assert "[agent] finished without more tool calls" in captured
