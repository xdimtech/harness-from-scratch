from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agents.s12_worktree_task_isolation import EventBus, TaskManager, WorktreeManager, detect_repo_root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_detect_repo_root_returns_repo_and_none_for_plain_dir(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()

    assert detect_repo_root(repo) == repo
    assert detect_repo_root(plain) is None


def test_task_bind_worktree_promotes_pending_task(tmp_path: Path) -> None:
    tasks = TaskManager(tmp_path / ".tasks")
    created = json.loads(tasks.create("Implement auth refactor", "split config loading"))

    bound = json.loads(tasks.bind_worktree(created["id"], "auth-refactor", owner="alice"))

    assert bound["worktree"] == "auth-refactor"
    assert bound["owner"] == "alice"
    assert bound["status"] == "in_progress"


def test_worktree_create_binds_task_and_records_events(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    tasks = TaskManager(repo / ".tasks")
    events = EventBus(repo / ".worktrees" / "events.jsonl")
    worktrees = WorktreeManager(repo, tasks, events)
    task = json.loads(tasks.create("Backend auth refactor"))

    created = json.loads(worktrees.create("auth-refactor", task_id=task["id"]))
    task_after = tasks.get_raw(task["id"])
    index = json.loads((repo / ".worktrees" / "index.json").read_text(encoding="utf-8"))
    recent_events = json.loads(events.list_recent())

    assert created["name"] == "auth-refactor"
    assert Path(created["path"]).exists()
    assert task_after["worktree"] == "auth-refactor"
    assert task_after["status"] == "in_progress"
    assert index["worktrees"][0]["task_id"] == task["id"]
    assert [event["event"] for event in recent_events] == [
        "worktree.create.before",
        "worktree.create.after",
    ]


def test_worktree_run_isolated_from_repo_root_and_keep_updates_index(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    tasks = TaskManager(repo / ".tasks")
    events = EventBus(repo / ".worktrees" / "events.jsonl")
    worktrees = WorktreeManager(repo, tasks, events)
    task = json.loads(tasks.create("Frontend login page"))
    created = json.loads(worktrees.create("ui-login", task_id=task["id"]))

    result = worktrees.run(
        "ui-login",
        "python3 -c \"from pathlib import Path; Path('lane.txt').write_text('hello', encoding='utf-8')\"",
    )
    kept = json.loads(worktrees.keep("ui-login"))

    assert result == "(no output)"
    assert not (repo / "lane.txt").exists()
    assert (Path(created["path"]) / "lane.txt").exists()
    assert kept["status"] == "kept"


def test_worktree_remove_can_complete_bound_task_and_emit_lifecycle_events(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    tasks = TaskManager(repo / ".tasks")
    events = EventBus(repo / ".worktrees" / "events.jsonl")
    worktrees = WorktreeManager(repo, tasks, events)
    task = json.loads(tasks.create("Auth cleanup"))
    created = json.loads(worktrees.create("auth-cleanup", task_id=task["id"]))

    removed = worktrees.remove("auth-cleanup", complete_task=True)
    task_after = tasks.get_raw(task["id"])
    index = json.loads((repo / ".worktrees" / "index.json").read_text(encoding="utf-8"))
    recent_events = json.loads(events.list_recent(limit=10))

    assert removed == "Removed worktree 'auth-cleanup'"
    assert not Path(created["path"]).exists()
    assert task_after["status"] == "completed"
    assert task_after["worktree"] == ""
    assert index["worktrees"][0]["status"] == "removed"
    assert [event["event"] for event in recent_events] == [
        "worktree.create.before",
        "worktree.create.after",
        "worktree.remove.before",
        "task.completed",
        "worktree.remove.after",
    ]
