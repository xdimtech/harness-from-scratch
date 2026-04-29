from __future__ import annotations

import subprocess
from pathlib import Path

from agents.repopilot_runtime import RepoPilotRuntime


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "RepoPilot Test"], cwd=root, check=True)
    (root / "README.md").write_text("# temp repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True, text=True)


def test_repopilot_runtime_creates_run_and_artifacts(tmp_path: Path) -> None:
    init_repo(tmp_path)
    runtime = RepoPilotRuntime(tmp_path)

    run = runtime.start_mission("Build isolated Python artifacts with QA and docs lanes")
    finished = runtime.wait_for_run(run["id"], timeout=45)

    assert finished["status"] == "completed"

    tasks = runtime.tasks.list_tasks(run["id"])
    assert len(tasks) == 3
    assert {task["status"] for task in tasks} == {"completed"}

    worktrees = runtime.worktrees.list_all(run["id"])
    assert len(worktrees) == 3
    assert all(Path(item["path"]).exists() for item in worktrees)

    backend = next(task for task in tasks if task["owner"] == "backend_dev")
    backend_wt = next(item for item in worktrees if item["name"] == backend["worktree"])
    feature_path = Path(backend_wt["path"]) / backend["artifact_paths"][1]
    assert feature_path.exists()
    assert "build_feature_summary" in feature_path.read_text(encoding="utf-8")

    jobs = runtime.jobs.list_jobs(run["id"])
    assert jobs
    assert any(job["status"] == "completed" for job in jobs)

    events = runtime.events.list_events(run["id"], limit=200)
    assert any(event["type"] == "run.completed" for event in events)
    assert Path(finished["summary_path"]).exists()


def test_repopilot_runtime_supports_repeated_missions_without_worktree_collisions(tmp_path: Path) -> None:
    init_repo(tmp_path)
    runtime = RepoPilotRuntime(tmp_path)

    first = runtime.start_mission("Build isolated Python artifacts with QA and docs lanes")
    second = runtime.start_mission("Build isolated Python artifacts with QA and docs lanes")

    finished_first = runtime.wait_for_run(first["id"], timeout=45)
    finished_second = runtime.wait_for_run(second["id"], timeout=45)

    assert finished_first["status"] == "completed"
    assert finished_second["status"] == "completed"

    first_tasks = runtime.tasks.list_tasks(first["id"])
    second_tasks = runtime.tasks.list_tasks(second["id"])
    assert {task["status"] for task in first_tasks} == {"completed"}
    assert {task["status"] for task in second_tasks} == {"completed"}

    first_worktrees = runtime.worktrees.list_all(first["id"])
    second_worktrees = runtime.worktrees.list_all(second["id"])
    assert len(first_worktrees) == 3
    assert len(second_worktrees) == 3

    names = {item["name"] for item in first_worktrees + second_worktrees}
    assert len(names) == 6

    second_events = runtime.events.list_events(second["id"], limit=200)
    assert any(event["type"] == "run.completed" for event in second_events)


def test_repopilot_runtime_supports_unborn_head_repositories(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "RepoPilot Test"], cwd=tmp_path, check=True)

    runtime = RepoPilotRuntime(tmp_path)
    run = runtime.start_mission("Build isolated Python artifacts with QA and docs lanes")
    finished = runtime.wait_for_run(run["id"], timeout=45)

    assert finished["status"] == "completed"

    worktrees = runtime.worktrees.list_all(run["id"])
    assert len(worktrees) == 3
    assert all(Path(item["path"]).exists() for item in worktrees)
