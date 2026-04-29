from __future__ import annotations

import subprocess
import time
from typing import Any
from pathlib import Path

from fastapi.testclient import TestClient

from products.repopilot_harness.backend.app.api import create_app
from products.repopilot_harness.backend.app.direct_llm_executor import DirectLLMExecutor
from products.repopilot_harness.backend.app.llm_client import AssistantTurn, ToolUseRequest


def init_repo(root: Path, with_commit: bool = True) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "RepoPilot Test"], cwd=root, check=True)
    (root / "README.md").write_text("# temp repo\n", encoding="utf-8")
    (root / "module.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    if not with_commit:
        return
    subprocess.run(["git", "add", "README.md", "module.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True, text=True)


def wait_for_status(client: TestClient, run_id: str, expected: set[str], timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        status = data["run"]["status"]
        if status in expected:
            return data
        time.sleep(0.2)
    return client.get(f"/api/runs/{run_id}").json()


class ScriptedLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                text="Inspecting the current Python module.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Inspecting the current Python module."},
                    {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "module.py"}},
                ],
                tool_uses=[ToolUseRequest(id="tool-1", name="read_file", input={"path": "module.py"})],
            )
        if self.calls == 2:
            return AssistantTurn(
                text="Writing the requested implementation.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Writing the requested implementation."},
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "write_file",
                        "input": {
                            "path": "module.py",
                            "content": (
                                "def add(a, b):\n"
                                "    return a + b\n\n"
                                "def subtract(a, b):\n"
                                "    return a - b\n"
                            ),
                        },
                    },
                ],
                tool_uses=[
                    ToolUseRequest(
                        id="tool-2",
                        name="write_file",
                        input={
                            "path": "module.py",
                            "content": (
                                "def add(a, b):\n"
                                "    return a + b\n\n"
                                "def subtract(a, b):\n"
                                "    return a - b\n"
                            ),
                        },
                    )
                ],
            )
        if self.calls == 3:
            return AssistantTurn(
                text="Running validation before finishing.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Running validation before finishing."},
                    {
                        "type": "tool_use",
                        "id": "tool-3",
                        "name": "run_command",
                        "input": {"command": "python3 -m py_compile module.py", "timeout_seconds": 60},
                    },
                ],
                tool_uses=[
                    ToolUseRequest(
                        id="tool-3",
                        name="run_command",
                        input={"command": "python3 -m py_compile module.py", "timeout_seconds": 60},
                    )
                ],
            )
        return AssistantTurn(
            text="Finishing the task.",
            stop_reason="tool_use",
            content_blocks=[
                {"type": "text", "text": "Finishing the task."},
                {
                    "type": "tool_use",
                    "id": "tool-4",
                    "name": "finish_task",
                    "input": {
                        "summary": "Added subtract(a, b) to module.py and validated it with python3 -m py_compile module.py."
                    },
                },
            ],
            tool_uses=[
                ToolUseRequest(
                    id="tool-4",
                    name="finish_task",
                    input={"summary": "Added subtract(a, b) to module.py and validated it with python3 -m py_compile module.py."},
                )
            ],
        )


class ScriptedRunCommandLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                text="Inspect the repo first.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Inspect the repo first."},
                    {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "module.py"}},
                ],
                tool_uses=[ToolUseRequest(id="tool-1", name="read_file", input={"path": "module.py"})],
            )
        if self.calls == 2:
            return AssistantTurn(
                text="Write the file through a shell command.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Write the file through a shell command."},
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "run_command",
                        "input": {
                            "command": (
                                "python3 - <<'PY'\n"
                                "from pathlib import Path\n"
                                "Path('module.py').write_text("
                                "\"def add(a, b):\\n    return a + b\\n\\n\""
                                "\"def subtract(a, b):\\n    return a - b\\n\", encoding='utf-8')\n"
                                "print('written')\n"
                                "PY"
                            ),
                            "timeout_seconds": 60,
                        },
                    },
                ],
                tool_uses=[
                    ToolUseRequest(
                        id="tool-2",
                        name="run_command",
                        input={
                            "command": (
                                "python3 - <<'PY'\n"
                                "from pathlib import Path\n"
                                "Path('module.py').write_text("
                                "\"def add(a, b):\\n    return a + b\\n\\n\""
                                "\"def subtract(a, b):\\n    return a - b\\n\", encoding='utf-8')\n"
                                "print('written')\n"
                                "PY"
                            ),
                            "timeout_seconds": 60,
                        },
                    )
                ],
            )
        if self.calls == 3:
            return AssistantTurn(
                text="Validate the rewritten file.",
                stop_reason="tool_use",
                content_blocks=[
                    {"type": "text", "text": "Validate the rewritten file."},
                    {
                        "type": "tool_use",
                        "id": "tool-3",
                        "name": "run_command",
                        "input": {"command": "python3 -m py_compile module.py", "timeout_seconds": 60},
                    },
                ],
                tool_uses=[
                    ToolUseRequest(
                        id="tool-3",
                        name="run_command",
                        input={"command": "python3 -m py_compile module.py", "timeout_seconds": 60},
                    )
                ],
            )
        return AssistantTurn(
            text="Finishing the task.",
            stop_reason="tool_use",
            content_blocks=[
                {"type": "text", "text": "Finishing the task."},
                {
                    "type": "tool_use",
                    "id": "tool-4",
                    "name": "finish_task",
                    "input": {
                        "summary": "Added subtract(a, b) to module.py via run_command and validated it with python3 -m py_compile module.py."
                    },
                },
            ],
            tool_uses=[
                ToolUseRequest(
                    id="tool-4",
                    name="finish_task",
                    input={
                        "summary": "Added subtract(a, b) to module.py via run_command and validated it with python3 -m py_compile module.py."
                    },
                )
            ],
        )


def test_repopilot_harness_run_lifecycle(tmp_path: Path) -> None:
    init_repo(tmp_path)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Build a backend delivery lane and validate it",
            "constraints": "Keep changes scoped to Python only.",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "scaffold",
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "planned"
    assert run["execution"]["mode"] == "scaffold"

    snapshot = client.get(f"/api/runs/{run['id']}").json()
    assert len(snapshot["tasks"]) >= 4

    approved = client.post(f"/api/runs/{run['id']}/approve")
    assert approved.status_code == 200, approved.text

    finished = wait_for_status(client, run["id"], {"completed", "failed"})
    assert finished["run"]["status"] == "completed"

    worktrees = finished["worktrees"]
    assert worktrees
    assert Path(worktrees[0]["path"]).exists()
    implementation_worktree = next(item for item in worktrees if item["workspace_type"] != "delivery_merge")

    tasks = finished["tasks"]
    assert any(task["kind"] == "implementation" for task in tasks)
    assert any(task["kind"] == "validation" for task in tasks)
    assert any(task["artifact_paths"] for task in tasks)

    jobs = finished["jobs"]
    assert jobs
    assert any(job["status"] == "completed" for job in jobs)

    files = client.get(f"/api/worktrees/{implementation_worktree['id']}/files").json()["items"]
    assert any(item["path"] == "REPOPILOT_TASK.md" for item in files)
    assert any(artifact["name"] == "validation_report.md" for artifact in finished["artifacts"])


def test_repopilot_harness_supports_repeated_runs_in_same_repo(tmp_path: Path) -> None:
    init_repo(tmp_path)
    app = create_app()
    client = TestClient(app)

    created_ids = []
    for _ in range(2):
        response = client.post(
            "/api/runs",
            json={
                "repo_root": str(tmp_path),
                "mission": "Build a backend delivery lane and validate it",
                "validation_commands": ["python3 -m py_compile module.py"],
                "execution_mode": "scaffold",
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()
        created_ids.append(run["id"])
        approved = client.post(f"/api/runs/{run['id']}/approve")
        assert approved.status_code == 200, approved.text
        finished = wait_for_status(client, run["id"], {"completed", "failed"})
        assert finished["run"]["status"] == "completed"

    assert len(set(created_ids)) == 2


def test_repopilot_harness_copies_uncommitted_repo_state_into_workspace(tmp_path: Path) -> None:
    init_repo(tmp_path, with_commit=False)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Build a core implementation lane and validate it",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "scaffold",
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()

    approved = client.post(f"/api/runs/{run['id']}/approve")
    assert approved.status_code == 200, approved.text
    finished = wait_for_status(client, run["id"], {"completed", "failed"})
    assert finished["run"]["status"] == "completed"

    worktree = finished["worktrees"][0]
    files = client.get(f"/api/worktrees/{worktree['id']}/files").json()["items"]
    assert any(item["path"] == "module.py" for item in files)
    assert worktree["workspace_type"] == "git_init_copy"


def test_repopilot_harness_runs_agent_command_for_implementation_tasks(tmp_path: Path) -> None:
    init_repo(tmp_path)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Build a backend delivery lane and validate it",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "agent_command",
            "agent_command": "python3 -c \"from pathlib import Path; Path('generated_by_agent.txt').write_text('ok', encoding='utf-8')\"",
            "agent_timeout_seconds": 300,
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()

    approved = client.post(f"/api/runs/{run['id']}/approve")
    assert approved.status_code == 200, approved.text
    finished = wait_for_status(client, run["id"], {"completed", "failed"})
    assert finished["run"]["status"] == "completed"

    jobs = finished["jobs"]
    assert any("generated_by_agent.txt" in job["command"] for job in jobs)
    worktree = finished["worktrees"][0]
    files = client.get(f"/api/worktrees/{worktree['id']}/files").json()["items"]
    assert any(item["path"] == "generated_by_agent.txt" for item in files)


def test_repopilot_harness_defaults_to_direct_llm_when_env_is_configured(tmp_path: Path, monkeypatch) -> None:
    init_repo(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "fake-model")
    monkeypatch.delenv("REPOPILOT_AGENT_COMMAND", raising=False)
    app = create_app()
    client = TestClient(app)

    meta = client.get("/api/meta").json()
    assert meta["default_execution_mode"] == "direct_llm"
    assert meta["default_model_id"] == "fake-model"


def test_repopilot_harness_runs_direct_llm_agent_for_implementation_tasks(tmp_path: Path, monkeypatch) -> None:
    init_repo(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "fake-model")
    monkeypatch.setattr(
        "products.repopilot_harness.backend.app.direct_llm_executor.create_llm_client",
        lambda: ScriptedLLMClient(),
    )
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Add subtract support to the sample module",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "direct_llm",
            "agent_timeout_seconds": 300,
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["execution"]["mode"] == "direct_llm"

    approved = client.post(f"/api/runs/{run['id']}/approve")
    assert approved.status_code == 200, approved.text
    finished = wait_for_status(client, run["id"], {"completed", "failed"})
    assert finished["run"]["status"] == "completed"

    worktree = finished["worktrees"][0]
    module_source = client.get(f"/api/file?path={worktree['path']}/module.py").json()["content"]
    assert "def subtract(a, b):" in module_source

    artifact_names = {artifact["name"] for artifact in finished["artifacts"]}
    assert "agent_trace.jsonl" in artifact_names
    assert "agent_delivery.md" in artifact_names
    assert any(event["type"] == "tool.called" for event in finished["events"])


def test_repopilot_harness_counts_run_command_file_writes_as_real_mutations(tmp_path: Path, monkeypatch) -> None:
    init_repo(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "fake-model")
    monkeypatch.setattr(
        "products.repopilot_harness.backend.app.direct_llm_executor.create_llm_client",
        lambda: ScriptedRunCommandLLMClient(),
    )
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Add subtract support to the sample module",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "direct_llm",
            "agent_timeout_seconds": 300,
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()

    approved = client.post(f"/api/runs/{run['id']}/approve")
    assert approved.status_code == 200, approved.text
    finished = wait_for_status(client, run["id"], {"completed", "failed"})
    assert finished["run"]["status"] == "completed"

    worktree = finished["worktrees"][0]
    module_source = client.get(f"/api/file?path={worktree['path']}/module.py").json()["content"]
    assert "def subtract(a, b):" in module_source


def test_direct_llm_text_action_parser_accepts_embedded_json_and_aliases() -> None:
    executor = DirectLLMExecutor(client=ScriptedLLMClient())
    action = executor._parse_text_action(
        "I'll inspect the workspace first.\n"
        '{"tool":"list_directory","input":{"path":"."}}\n'
        '{"tool":"read_file","input":{"path":"README.md"}}'
    )
    assert action == {"tool": "list_files", "input": {"path": "."}}


def test_direct_llm_text_action_parser_accepts_block_protocol() -> None:
    executor = DirectLLMExecutor(client=ScriptedLLMClient())
    action = executor._parse_text_action(
        "ACTION: run_command\n"
        "COMMAND <<'CMD'\n"
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('app.py').write_text('print(\"ok\")\\n', encoding='utf-8')\n"
        "PY\n"
        "CMD\n"
    )
    assert action == {
        "tool": "run_command",
        "input": {
            "command": (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "Path('app.py').write_text('print(\"ok\")\\n', encoding='utf-8')\n"
                "PY"
            )
        },
    }


def test_direct_llm_text_action_parser_accepts_content_tail_blocks() -> None:
    executor = DirectLLMExecutor(client=ScriptedLLMClient())
    action = executor._parse_text_action(
        "ACTION: write_file\n"
        "PATH: app.py\n"
        "CONTENT:\n"
        "print('hello')\n"
        "print('world')\n"
    )
    assert action == {
        "tool": "write_file",
        "input": {"path": "app.py", "content": "print('hello')\nprint('world')"},
    }


def test_direct_llm_compacts_large_text_actions_in_conversation_history() -> None:
    executor = DirectLLMExecutor(client=ScriptedLLMClient())
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('app.py').write_text('print(\"hello\")\\n' * 200, encoding='utf-8')\n"
        "PY"
    )
    compact = executor._conversation_assistant_content(
        f"ACTION: run_command\nCOMMAND <<'CMD'\n{command}\nCMD\n",
        {"tool": "run_command", "input": {"command": command, "timeout_seconds": 120}},
    )
    assert compact.startswith("ACTION: run_command")
    assert "chars omitted from history" in compact
    assert "Path('app.py').write_text" not in compact


def test_direct_llm_tool_feedback_truncates_large_outputs() -> None:
    executor = DirectLLMExecutor(client=ScriptedLLMClient())
    feedback = executor._conversation_tool_feedback("read_file", "x" * 5000)
    assert feedback.startswith("Tool result for read_file:")
    assert "...[truncated by RepoPilot]" in feedback
    assert len(feedback) < 4300


def test_planner_does_not_treat_build_as_ui_keyword(tmp_path: Path) -> None:
    init_repo(tmp_path)
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "repo_root": str(tmp_path),
            "mission": "Build a local Python command-line MBTI personality analyzer",
            "validation_commands": ["python3 -m py_compile module.py"],
            "execution_mode": "scaffold",
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()

    snapshot = client.get(f"/api/runs/{run['id']}").json()
    implementation_tasks = [task for task in snapshot["tasks"] if task["kind"] == "implementation"]
    assert [task["title"] for task in implementation_tasks] == ["Core implementation lane"]
