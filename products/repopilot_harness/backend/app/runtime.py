from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .direct_llm_executor import DirectLLMExecutor
from .domain import dependencies_satisfied, next_task_status
from .llm_client import llm_runtime_config
from .planner import MissionPlanner
from .store import EventStore, JobStore, RunStore, TaskStore, WorktreeStore
from .util import atomic_write_text, detect_repo_root, is_relative_to, now_ts, read_json, slugify, summarize_repo

DANGEROUS_FRAGMENTS = ["rm -rf /", "sudo ", " shutdown", " reboot", "> /dev/"]
SNAPSHOT_EXCLUDED_NAMES = {
    ".git",
    ".repopilot",
    ".repopilot_harness",
    ".repopilot_harness_worktrees",
    ".tasks",
    ".team",
    ".transcripts",
    ".worktrees",
    ".pytest_cache",
    "__pycache__",
}
DELIVERY_META_FILES = {"REPOPILOT_TASK.md", "REPOPILOT_EXECUTION_PROMPT.md"}
SUPPORTED_EXECUTION_MODES = {"scaffold", "agent_command", "direct_llm"}
RECOVERY_GRACE_SECONDS = 45.0


class RepoHarnessRuntime:
    def __init__(self, repo_root: Path):
        self.repo_root = detect_repo_root(repo_root)
        self.data_root = self.repo_root / ".repopilot_harness"
        self.workspaces_root = self.repo_root / ".repopilot_harness_worktrees"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self.events = EventStore(self.data_root / "events.jsonl")
        self.runs = RunStore(self.data_root)
        self.tasks = TaskStore(self.data_root)
        self.jobs = JobStore(self.data_root)
        self.worktrees = WorktreeStore(self.data_root)
        self.planner = MissionPlanner()
        self._threads: dict[str, threading.Thread] = {}
        self._active_tasks: dict[str, dict[int, threading.Thread]] = {}
        self._lock = threading.RLock()
        self._direct_llm_executor: DirectLLMExecutor | None = None
        self.session_path = self.data_root / "runtime_session.json"
        self._initialize_runtime_session()

    def create_run(
        self,
        *,
        mission: str,
        constraints: str = "",
        validation_commands: list[str] | None = None,
        execution_mode: str = "scaffold",
        agent_command: str = "",
        agent_timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        mission = mission.strip()
        if not mission:
            raise ValueError("mission is required")
        validation_commands = [cmd.strip() for cmd in (validation_commands or []) if cmd.strip()]
        if not validation_commands:
            validation_commands = self._default_validation_commands()

        execution = self._normalize_execution(
            execution_mode=execution_mode,
            agent_command=agent_command,
            agent_timeout_seconds=agent_timeout_seconds,
        )
        plan = self.planner.plan(
            mission=mission,
            repo_root=self.repo_root,
            constraints=constraints.strip(),
            validation_commands=validation_commands,
        )
        run = self.runs.create(
            {
                "slug": plan["plan_slug"],
                "mission": mission,
                "repo_root": str(self.repo_root),
                "operator_constraints": constraints.strip(),
                "validation_commands": validation_commands,
                "execution": execution,
                "plan": plan,
                "summary": "",
                "task_ids": [],
                "agents": {},
                "last_error": "",
            }
        )
        self._materialize_tasks(run, plan["tasks"])
        run = self.runs.get(run["id"])
        run["status"] = "planned"
        run["updated_at"] = now_ts()
        self.runs.save(run)
        self.events.emit(
            "run.planned",
            run_id=run["id"],
            mission=mission,
            summary=plan["summary"],
            execution_mode=execution["mode"],
        )
        return run

    def _normalize_execution(
        self,
        *,
        execution_mode: str,
        agent_command: str,
        agent_timeout_seconds: int,
    ) -> dict[str, Any]:
        command = agent_command.strip()
        mode = execution_mode.strip() or "scaffold"
        if command and mode == "scaffold":
            mode = "agent_command"
        if mode not in SUPPORTED_EXECUTION_MODES:
            raise ValueError(f"Unsupported execution_mode: {mode}")
        if mode == "agent_command" and not command:
            raise ValueError("agent_command is required when execution_mode is agent_command")
        timeout = max(60, min(int(agent_timeout_seconds or 900), 7200))
        default_llm_steps = "18"
        if mode == "direct_llm" and llm_runtime_config().get("uses_proxy"):
            default_llm_steps = "28"
        llm_max_steps = max(4, min(int(os.getenv("REPOPILOT_LLM_MAX_STEPS", default_llm_steps)), 40))
        return {
            "mode": mode,
            "agent_command": command,
            "agent_timeout_seconds": timeout,
            "llm_max_steps": llm_max_steps,
        }

    def _default_validation_commands(self) -> list[str]:
        repo = self.repo_root
        commands: list[str] = []
        if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists():
            commands.append("python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')")
        if (repo / "package.json").exists():
            commands.append("npm test -- --runInBand")
        return commands or ["git status --short"]

    def _materialize_tasks(self, run: dict[str, Any], task_specs: list[dict[str, Any]]) -> None:
        index_to_task_id: dict[int, int] = {}
        for spec in task_specs:
            task = self.tasks.create(
                {
                    "run_id": run["id"],
                    "title": spec["title"],
                    "description": spec["description"],
                    "kind": spec["kind"],
                    "role_required": spec["role_required"],
                    "focus": spec.get("focus", ""),
                    "depends_on": [],
                    "artifact_paths": [],
                    "worktree_id": "",
                    "job_ids": [],
                    "owner_agent_id": "",
                    "acceptance_criteria": spec.get("acceptance_criteria", []),
                    "commands": spec.get("commands", []),
                    "error": "",
                    "phase": "Planned",
                }
            )
            index_to_task_id[len(index_to_task_id) + 1] = task["id"]
            run["task_ids"].append(task["id"])
        for index, spec in enumerate(task_specs, start=1):
            task_id = index_to_task_id[index]
            task = self.tasks.get(str(task_id))
            task["depends_on"] = [index_to_task_id[item] for item in spec.get("depends_on", []) if item in index_to_task_id]
            task["updated_at"] = now_ts()
            self.tasks.save(task)
        run["updated_at"] = now_ts()
        self.runs.save(run)

    def list_runs(self) -> list[dict[str, Any]]:
        items = self.runs.list()
        items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return items

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id)

    def list_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return [task for task in self.tasks.list() if task.get("run_id") == run_id]

    def list_jobs(self, run_id: str) -> list[dict[str, Any]]:
        jobs = [job for job in self.jobs.list() if job.get("run_id") == run_id]
        jobs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return jobs

    def list_worktrees(self, run_id: str) -> list[dict[str, Any]]:
        items = [item for item in self.worktrees.list() if item.get("run_id") == run_id]
        items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return items

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        tasks = self.list_tasks(run_id)
        artifacts: list[dict[str, Any]] = []
        for task in tasks:
            for path in task.get("artifact_paths", []):
                artifact_path = Path(path)
                artifacts.append(
                    {
                        "task_id": task["id"],
                        "title": task["title"],
                        "path": str(artifact_path),
                        "name": artifact_path.name,
                        "exists": artifact_path.exists(),
                    }
                )
        return {
            "run": run,
            "tasks": tasks,
            "jobs": self.list_jobs(run_id),
            "worktrees": self.list_worktrees(run_id),
            "events": self.events.list(run_id, limit=300),
            "artifacts": artifacts,
        }

    def approve_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"planned", "paused", "failed"}:
            raise ValueError(f"Run cannot be approved from status {run['status']}")
        existing = self._threads.get(run_id)
        if existing and existing.is_alive():
            return run
        run["status"] = "approved"
        run["last_error"] = ""
        run["updated_at"] = now_ts()
        self.runs.save(run)
        self.events.emit("run.approved", run_id=run_id)
        thread = threading.Thread(target=self._run_loop, args=(run_id,), daemon=True)
        self._threads[run_id] = thread
        thread.start()
        return self.get_run(run_id)

    def retry_task(self, task_id: int) -> dict[str, Any]:
        task = self.tasks.get(str(task_id))
        if task["status"] not in {"failed", "blocked", "review_required"}:
            raise ValueError("Only failed, blocked, or review_required tasks can be retried")
        task["status"] = "ready"
        task["phase"] = "Queued for retry"
        task["error"] = ""
        task["updated_at"] = now_ts()
        self.tasks.save(task)
        self.events.emit("task.retried", run_id=task["run_id"], task_id=task["id"], title=task["title"])
        run = self.get_run(task["run_id"])
        if run["status"] in {"failed", "paused"}:
            self.approve_run(run["id"])
        return self.tasks.get(str(task_id))

    def pause_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        run["status"] = "paused"
        run["updated_at"] = now_ts()
        self.runs.save(run)
        self.events.emit("run.paused", run_id=run_id)
        return run

    def wait_for_run(self, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.get_run(run_id)
            if run["status"] in {"completed", "failed", "paused"}:
                return run
            time.sleep(0.2)
        return self.get_run(run_id)

    def _recover_inflight_state(self) -> None:
        cutoff = now_ts() - RECOVERY_GRACE_SECONDS
        for run in self.runs.list():
            changed = False
            if run.get("status") in {"approved", "running"} and float(run.get("updated_at") or 0) < cutoff:
                run["status"] = "paused"
                run["last_error"] = "Recovered after process restart. Re-approve this run to continue."
                run["updated_at"] = now_ts()
                self.runs.save(run)
                changed = True
            for task in self.list_tasks(run["id"]):
                if (
                    task.get("status") in {"claimed", "executing", "validating"}
                    and float(task.get("updated_at") or 0) < cutoff
                ):
                    task["status"] = "ready"
                    task["phase"] = "Recovered after process restart"
                    task["owner_agent_id"] = ""
                    task["updated_at"] = now_ts()
                    self.tasks.save(task)
                    changed = True
            for job in self.list_jobs(run["id"]):
                if job.get("status") in {"queued", "running"} and float(job.get("updated_at") or 0) < cutoff:
                    output = (job.get("output") or "").rstrip()
                    job["status"] = "failed"
                    job["output"] = (output + "\n[RepoPilot] Job recovered after process restart before completion.").strip()
                    job["updated_at"] = now_ts()
                    job["finished_at"] = now_ts()
                    self.jobs.save(job)
                    changed = True
            if changed:
                self.events.emit("run.recovered", run_id=run["id"], status=run.get("status"))

    def _initialize_runtime_session(self) -> None:
        previous = {}
        if self.session_path.exists():
            try:
                previous = read_json(self.session_path)
            except Exception:
                previous = {}
        previous_pid = int(previous.get("pid") or 0)
        if not previous_pid or not self._pid_alive(previous_pid):
            self._recover_inflight_state()
        atomic_write_text(
            self.session_path,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "repo_root": str(self.repo_root),
                    "updated_at": now_ts(),
                },
                indent=2,
                ensure_ascii=True,
            )
            + "\n",
        )

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _run_loop(self, run_id: str) -> None:
        run = self.get_run(run_id)
        with self._lock:
            if run["status"] not in {"approved", "running"}:
                return
        run["status"] = "running"
        run["updated_at"] = now_ts()
        self.runs.save(run)
        self.events.emit("run.started", run_id=run_id, mission=run["mission"])

        active = self._active_tasks.setdefault(run_id, {})
        while True:
            run = self.get_run(run_id)
            if run["status"] == "paused":
                return

            tasks = self.list_tasks(run_id)
            self._refresh_ready_tasks(tasks)
            tasks = self.list_tasks(run_id)
            for task_id, thread in list(active.items()):
                if not thread.is_alive():
                    active.pop(task_id, None)

            ready_tasks = [task for task in tasks if task["status"] == "ready"]
            for task in ready_tasks:
                if len(active) >= 3:
                    break
                thread = threading.Thread(target=self._execute_task, args=(run_id, task["id"]), daemon=True)
                active[task["id"]] = thread
                thread.start()

            tasks = self.list_tasks(run_id)
            statuses = {task["status"] for task in tasks}
            if tasks and statuses <= {"completed"}:
                summary = self._publish_run_summary(run_id)
                run = self.get_run(run_id)
                run["status"] = "completed"
                run["summary"] = summary
                run["updated_at"] = now_ts()
                self.runs.save(run)
                self.events.emit("run.completed", run_id=run_id)
                return

            if not active and not ready_tasks:
                blocked = any(task["status"] in {"failed", "blocked", "review_required"} for task in tasks)
                if blocked:
                    run = self.get_run(run_id)
                    run["status"] = "failed"
                    run["last_error"] = "One or more tasks failed. Inspect task and job logs."
                    run["updated_at"] = now_ts()
                    self.runs.save(run)
                    self.events.emit("run.failed", run_id=run_id)
                    return

            time.sleep(0.25)

    def _refresh_ready_tasks(self, tasks: list[dict[str, Any]]) -> None:
        by_id = {task["id"]: task for task in tasks}
        for task in tasks:
            if task["status"] != "todo":
                continue
            dep_statuses = [by_id[dep]["status"] for dep in task.get("depends_on", []) if dep in by_id]
            if not task.get("depends_on"):
                task["status"] = next_task_status("todo", "ready")
                task["phase"] = "Ready to start"
                task["updated_at"] = now_ts()
                self.tasks.save(task)
                self.events.emit("task.ready", run_id=task["run_id"], task_id=task["id"], title=task["title"])
                continue
            if any(status in {"failed", "blocked", "canceled"} for status in dep_statuses):
                task["status"] = "blocked"
                task["phase"] = "Blocked by dependency"
                task["error"] = "A dependency failed or was blocked"
                task["updated_at"] = now_ts()
                self.tasks.save(task)
                self.events.emit("task.blocked", run_id=task["run_id"], task_id=task["id"], title=task["title"])
                continue
            if dependencies_satisfied(dep_statuses):
                task["status"] = next_task_status("todo", "ready")
                task["phase"] = "Ready to start"
                task["updated_at"] = now_ts()
                self.tasks.save(task)
                self.events.emit("task.ready", run_id=task["run_id"], task_id=task["id"], title=task["title"])

    def _update_task(self, task_id: int, **changes: Any) -> dict[str, Any]:
        task = self.tasks.get(str(task_id))
        task.update(changes)
        task["updated_at"] = now_ts()
        self.tasks.save(task)
        return task

    def _execute_task(self, run_id: str, task_id: int) -> None:
        task = self.tasks.get(str(task_id))
        agent_id = f"{task['role_required']}-{task['id']}"
        try:
            task = self._update_task(
                task_id,
                status=next_task_status(task["status"], "claimed"),
                owner_agent_id=agent_id,
                phase="Claimed by runtime",
                error="",
            )
            self._set_agent(run_id, agent_id, "claimed", f"Claimed {task['title']}", task_id=task_id)
            self.events.emit("task.claimed", run_id=run_id, task_id=task_id, agent_id=agent_id)

            task = self._update_task(
                task_id,
                status=next_task_status(task["status"], "executing"),
                phase=f"Executing {task['kind']} lane",
            )
            self.events.emit("task.executing", run_id=run_id, task_id=task_id, agent_id=agent_id)

            if task["kind"] == "analysis":
                artifacts = self._run_analysis(task)
                final_status = "completed"
            elif task["kind"] == "implementation":
                artifacts = self._run_implementation(task, agent_id)
                final_status = "completed"
            elif task["kind"] == "validation":
                task = self._update_task(
                    task_id,
                    status=next_task_status(task["status"], "validating"),
                    phase="Running validation commands",
                )
                self.events.emit("task.validating", run_id=run_id, task_id=task_id, agent_id=agent_id)
                artifacts = self._run_validation(task, agent_id)
                final_status = "completed"
            elif task["kind"] == "documentation":
                artifacts = self._run_documentation(task)
                final_status = "completed"
            else:
                raise ValueError(f"Unsupported task kind: {task['kind']}")

            task = self.tasks.get(str(task_id))
            task["artifact_paths"] = sorted(set(task.get("artifact_paths", []) + artifacts))
            task["status"] = next_task_status(task["status"], final_status)
            task["phase"] = "Completed"
            task["updated_at"] = now_ts()
            self.tasks.save(task)
            self._set_agent(run_id, agent_id, "idle", f"Finished {task['title']}", task_id=task_id)
            self.events.emit("task.finished", run_id=run_id, task_id=task_id, agent_id=agent_id, status=task["status"])
        except Exception as exc:
            task = self.tasks.get(str(task_id))
            task["status"] = "failed"
            task["error"] = str(exc)
            task["phase"] = "Failed"
            task["updated_at"] = now_ts()
            self.tasks.save(task)
            self._set_agent(run_id, agent_id, "error", str(exc), task_id=task_id)
            self.events.emit("task.failed", run_id=run_id, task_id=task_id, agent_id=agent_id, error=str(exc))

    def _artifacts_dir(self, run_id: str, task_id: int) -> Path:
        path = self.data_root / "artifacts" / run_id / f"task_{task_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _llm_executor(self) -> DirectLLMExecutor:
        if self._direct_llm_executor is None:
            self._direct_llm_executor = DirectLLMExecutor()
        return self._direct_llm_executor

    def _run_analysis(self, task: dict[str, Any]) -> list[str]:
        repo = summarize_repo(self.repo_root)
        artifact_dir = self._artifacts_dir(task["run_id"], task["id"])
        brief = artifact_dir / "mission_brief.md"
        run = self.get_run(task["run_id"])
        plan_lines = [
            "# Mission Brief",
            "",
            f"Mission: {run['mission']}",
            f"Repository: {repo['repo_root']}",
            f"Execution mode: {run.get('execution', {}).get('mode', 'scaffold')}",
            "",
            "## Constraints",
            run.get("operator_constraints") or "(none)",
            "",
            "## Top-level entries",
            *[f"- {item}" for item in repo["top_level"]],
            "",
            "## Sample files",
            *[f"- {item}" for item in repo["sample_files"]],
        ]
        atomic_write_text(brief, "\n".join(plan_lines) + "\n")
        return [str(brief)]

    def _run_implementation(self, task: dict[str, Any], agent_id: str) -> list[str]:
        worktree = self._create_worktree(task, agent_id)
        worktree_root = Path(worktree["path"])
        copied_files = self._sync_repo_snapshot(task, worktree_root)
        artifact_dir = self._artifacts_dir(task["run_id"], task["id"])
        summary_path = artifact_dir / "implementation_summary.md"
        brief_path = worktree_root / "REPOPILOT_TASK.md"
        prompt_path = worktree_root / "REPOPILOT_EXECUTION_PROMPT.md"
        handoff_dir = worktree_root / ".repopilot"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        notes_path = handoff_dir / f"task_{task['id']}_notes.md"
        run = self.get_run(task["run_id"])
        execution = run.get(
            "execution",
            {"mode": "scaffold", "agent_command": "", "agent_timeout_seconds": 900, "llm_max_steps": 18},
        )
        criteria = "\n".join(f"- {item}" for item in task.get("acceptance_criteria", []))
        atomic_write_text(
            brief_path,
            (
                f"# {task['title']}\n\n"
                f"Role: {task['role_required']}\n"
                f"Focus: {task.get('focus', 'general')}\n"
                f"Mission: {run['mission']}\n\n"
                f"## Description\n{task['description']}\n\n"
                f"## Acceptance Criteria\n{criteria}\n"
            ),
        )
        atomic_write_text(
            prompt_path,
            (
                f"You are executing RepoPilot task #{task['id']} in {worktree['path']}.\n\n"
                f"Mission: {run['mission']}\n\n"
                f"Constraints:\n{run.get('operator_constraints') or '(none)'}\n\n"
                f"Task title: {task['title']}\n"
                f"Task description: {task['description']}\n"
                f"Focus: {task.get('focus', 'general')}\n\n"
                f"Acceptance criteria:\n{criteria}\n\n"
                "Read REPOPILOT_TASK.md first, then make concrete code changes in this worktree only."
            ),
        )
        atomic_write_text(
            notes_path,
            (
                "# Implementation Lane Notes\n\n"
                f"This workspace belongs to task #{task['id']} ({task['title']}).\n\n"
                f"- Agent: {agent_id}\n"
                f"- Workspace: {worktree['path']}\n"
                f"- Branch: {worktree['branch']}\n"
                f"- Workspace type: {worktree['workspace_type']}\n"
                f"- Mission: {run['mission']}\n"
                f"- Execution mode: {execution['mode']}\n"
                f"- Snapshot file count: {copied_files}\n"
            ),
        )

        task = self._update_task(
            task["id"],
            worktree_id=worktree["id"],
            artifact_paths=sorted(set(task.get("artifact_paths", []) + [str(brief_path), str(prompt_path), str(notes_path)])),
            phase="Workspace prepared",
        )

        job_ids = list(task.get("job_ids", []))
        direct_llm_summary = ""
        direct_llm_artifacts: list[str] = []
        if execution["mode"] == "direct_llm":
            self.events.emit(
                "task.direct_llm.started",
                run_id=task["run_id"],
                task_id=task["id"],
                model=os.getenv("MODEL_ID", ""),
            )
            self._update_task(task["id"], phase="Direct LLM agent is coding in workspace")
            result = self._llm_executor().execute(
                runtime=self,
                run=run,
                task=task,
                agent_id=agent_id,
                worktree=worktree,
                artifact_dir=artifact_dir,
                prompt_path=prompt_path,
                task_brief_path=brief_path,
                notes_path=notes_path,
                max_steps=execution.get("llm_max_steps", 18),
            )
            direct_llm_summary = result.summary
            direct_llm_artifacts.extend(result.artifact_paths)
            self._update_task(task["id"], phase=f"Direct LLM execution finished in {result.step_count} steps")
        elif execution["mode"] == "agent_command":
            self.events.emit(
                "task.agent_command.started",
                run_id=task["run_id"],
                task_id=task["id"],
                command=execution["agent_command"],
            )
            job = self._run_job(
                run_id=task["run_id"],
                task_id=task["id"],
                agent_id=agent_id,
                command=execution["agent_command"],
                cwd=worktree_root,
                env={
                    "REPOPILOT_TASK_ID": str(task["id"]),
                    "REPOPILOT_TASK_TITLE": task["title"],
                    "REPOPILOT_TASK_KIND": task["kind"],
                    "REPOPILOT_MISSION": run["mission"],
                    "REPOPILOT_REPO_ROOT": str(self.repo_root),
                    "REPOPILOT_WORKTREE": str(worktree_root),
                    "REPOPILOT_TASK_FILE": str(brief_path),
                    "REPOPILOT_PROMPT_FILE": str(prompt_path),
                    "REPOPILOT_NOTES_FILE": str(notes_path),
                },
                timeout=execution.get("agent_timeout_seconds", 900),
            )
            job_ids.append(job["id"])
            self._update_task(task["id"], job_ids=sorted(set(job_ids)), phase="Agent execution finished")
            if job["status"] != "completed":
                raise ValueError(f"Implementation agent command failed; inspect job {job['id']}")

        diff = self._git_output(["status", "--short"], cwd=worktree_root)
        atomic_write_text(
            summary_path,
            "".join(
                [
                    "# Implementation Summary\n\n",
                    f"Task: {task['title']}\n\n",
                    f"Workspace: `{worktree['path']}`\n\n",
                    f"Execution mode: `{execution['mode']}`\n\n",
                    "## Produced artifacts\n",
                    f"- `{brief_path}`\n",
                    f"- `{prompt_path}`\n",
                    f"- `{notes_path}`\n",
                    *[f"- `{path}`\n" for path in direct_llm_artifacts],
                    "\n",
                    f"## Snapshot sync\n\nCopied files: `{copied_files}`\n\n",
                    *( [f"## Direct LLM Summary\n\n{direct_llm_summary}\n\n"] if direct_llm_summary else [] ),
                    f"## Git status\n\n```text\n{diff}\n```\n",
                ]
            ),
        )
        return [str(summary_path), str(brief_path), str(prompt_path), str(notes_path), *direct_llm_artifacts]

    def _run_validation(self, task: dict[str, Any], agent_id: str) -> list[str]:
        deps = [self.tasks.get(str(dep_id)) for dep_id in task.get("depends_on", [])]
        worktrees = []
        for dep in deps:
            worktree_id = dep.get("worktree_id")
            if worktree_id:
                worktrees.append(self.worktrees.get(worktree_id))
        if not worktrees:
            raise ValueError("No implementation workspaces available for validation")

        artifact_dir = self._artifacts_dir(task["run_id"], task["id"])
        report_path = artifact_dir / "validation_report.md"
        merge_report_path = artifact_dir / "delivery_merge_report.md"
        report_lines = ["# Validation Report", ""]
        merge_lines = ["# Delivery Merge Report", ""]
        any_failure = False
        job_ids: list[str] = []
        validation_targets = worktrees
        merge_artifacts: list[str] = []
        if len(worktrees) > 1:
            delivery_workspace, merge_summary = self._create_delivery_workspace(task=task, agent_id=agent_id, worktrees=worktrees)
            applied_lines = [f"- `{path}`" for path in merge_summary["applied_paths"]] or ["- (none)"]
            conflict_lines = [f"- `{path}`" for path in merge_summary["conflicts"]] or ["- (none)"]
            deleted_lines = [f"- `{path}`" for path in merge_summary["deleted_paths"]] or ["- (none)"]
            validation_targets = [delivery_workspace]
            merge_lines.extend(
                [
                    f"Delivery workspace: `{delivery_workspace['path']}`",
                    "",
                    "## Source workspaces",
                    *[f"- `{item['name']}` -> `{item['path']}`" for item in worktrees],
                    "",
                    "## Applied paths",
                    *applied_lines,
                    "",
                    "## Conflicts resolved by last-writer-wins",
                    *conflict_lines,
                    "",
                    "## Deleted paths",
                    *deleted_lines,
                    "",
                ]
            )
            atomic_write_text(merge_report_path, "\n".join(merge_lines))
            merge_artifacts.append(str(merge_report_path))
        for worktree in validation_targets:
            report_lines.append(f"## Workspace `{worktree['name']}`")
            report_lines.append("")
            for command in task.get("commands", []):
                job = self._run_job(
                    run_id=task["run_id"],
                    task_id=task["id"],
                    agent_id=agent_id,
                    command=command,
                    cwd=Path(worktree["path"]),
                    timeout=600,
                )
                job_ids.append(job["id"])
                report_lines.append(f"### `{command}`")
                report_lines.append("")
                report_lines.append(f"- status: {job['status']}")
                report_lines.append(f"- exit_code: {job['exit_code']}")
                report_lines.append(f"- cwd: {job['cwd']}")
                report_lines.append("")
                report_lines.append("```text")
                report_lines.append(job["output"] or "(no output)")
                report_lines.append("```")
                report_lines.append("")
                if job["status"] != "completed":
                    any_failure = True
        atomic_write_text(report_path, "\n".join(report_lines))
        current = self.tasks.get(str(task["id"]))
        current["job_ids"] = sorted(set(current.get("job_ids", []) + job_ids))
        current["phase"] = "Validation report written"
        current["updated_at"] = now_ts()
        self.tasks.save(current)
        if any_failure:
            raise ValueError(f"Validation failed; inspect {report_path}")
        return [str(report_path), *merge_artifacts]

    def _run_documentation(self, task: dict[str, Any]) -> list[str]:
        artifact_dir = self._artifacts_dir(task["run_id"], task["id"])
        handoff = artifact_dir / "delivery_handoff.md"
        run = self.get_run(task["run_id"])
        tasks = self.list_tasks(task["run_id"])
        jobs = self.list_jobs(task["run_id"])
        worktrees = self.list_worktrees(task["run_id"])
        lines = [
            "# Delivery Handoff",
            "",
            f"Mission: {run['mission']}",
            f"Execution mode: {run.get('execution', {}).get('mode', 'scaffold')}",
            "",
            "## Task outcomes",
        ]
        for item in tasks:
            lines.append(f"- #{item['id']} {item['title']} -> {item['status']} ({item.get('phase', '')})")
        lines += ["", "## Validation jobs"]
        for job in jobs:
            lines.append(f"- {job['command']} -> {job['status']} ({job['cwd']})")
        lines += ["", "## Workspaces"]
        for worktree in worktrees:
            lines.append(f"- {worktree['name']} [{worktree.get('workspace_type', 'git_worktree')}] -> {worktree['path']}")
        atomic_write_text(handoff, "\n".join(lines) + "\n")
        return [str(handoff)]

    def _publish_run_summary(self, run_id: str) -> str:
        run = self.get_run(run_id)
        tasks = self.list_tasks(run_id)
        jobs = self.list_jobs(run_id)
        worktrees = self.list_worktrees(run_id)
        summary_path = self.data_root / "summaries" / f"{run_id}.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Run Summary: {run['mission']}",
            "",
            f"Status: {run['status']}",
            f"Execution mode: {run.get('execution', {}).get('mode', 'scaffold')}",
            "",
            "## Tasks",
        ]
        for task in tasks:
            lines.append(f"- #{task['id']} {task['title']} [{task['status']}] - {task.get('phase', '')}")
        if jobs:
            lines.extend(["", "## Jobs"])
            for job in jobs:
                lines.append(f"- {job['command']} -> {job['status']}")
        if worktrees:
            lines.extend(["", "## Workspaces"])
            for worktree in worktrees:
                lines.append(f"- {worktree['name']} [{worktree.get('workspace_type', 'git_worktree')}] -> {worktree['path']}")
        atomic_write_text(summary_path, "\n".join(lines) + "\n")
        return str(summary_path)

    def _create_worktree(self, task: dict[str, Any], agent_id: str) -> dict[str, Any]:
        name = f"{slugify(self.get_run(task['run_id'])['slug'])}-task-{task['id']}-{slugify(task['focus'] or 'work')}-{int(time.time() * 1000) % 1_000_000}"
        branch = f"repopilot/{name}"
        path = self.workspaces_root / name
        workspace_type = "git_worktree"
        if self._has_head():
            self._run_git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        else:
            path.mkdir(parents=True, exist_ok=True)
            init_result = subprocess.run(["git", "init", "-b", branch], cwd=path, capture_output=True, text=True, timeout=60)
            if init_result.returncode != 0:
                raise RuntimeError((init_result.stdout + init_result.stderr).strip() or "git init failed")
            workspace_type = "git_init_copy"
        worktree = self.worktrees.create(
            {
                "name": name,
                "path": str(path),
                "branch": branch,
                "run_id": task["run_id"],
                "task_id": task["id"],
                "owner_agent_id": agent_id,
                "workspace_type": workspace_type,
            }
        )
        self.events.emit(
            "worktree.created",
            run_id=task["run_id"],
            task_id=task["id"],
            worktree_id=worktree["id"],
            path=str(path),
            workspace_type=workspace_type,
        )
        return worktree

    def _create_delivery_workspace(
        self,
        *,
        task: dict[str, Any],
        agent_id: str,
        worktrees: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        run = self.get_run(task["run_id"])
        name = (
            f"{slugify(run['slug'])}-delivery-{task['id']}-"
            f"{int(time.time() * 1000) % 1_000_000}"
        )
        path = self.workspaces_root / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

        delivery_workspace = self.worktrees.create(
            {
                "name": name,
                "path": str(path),
                "branch": "",
                "run_id": task["run_id"],
                "task_id": task["id"],
                "owner_agent_id": agent_id,
                "workspace_type": "delivery_merge",
                "merged_from_worktree_ids": [item["id"] for item in worktrees],
            }
        )

        self._copy_workspace_tree(Path(worktrees[0]["path"]), path)
        source_by_path: dict[str, str] = {}
        applied_paths: list[str] = []
        deleted_paths: list[str] = []
        conflicts: list[str] = []

        for worktree in sorted(worktrees, key=lambda item: (item.get("task_id", 0), item["id"])):
            changed_paths, removed_paths = self._workspace_changed_paths(Path(worktree["path"]))
            for rel_text in changed_paths:
                rel_path = Path(rel_text)
                if self._should_skip_delivery_path(rel_path):
                    continue
                source = Path(worktree["path"]) / rel_path
                if not source.exists() or not source.is_file():
                    continue
                target = path / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if rel_text in source_by_path and source_by_path[rel_text] != worktree["id"]:
                    conflicts.append(rel_text)
                source_by_path[rel_text] = worktree["id"]
                applied_paths.append(rel_text)
            for rel_text in removed_paths:
                rel_path = Path(rel_text)
                if self._should_skip_delivery_path(rel_path):
                    continue
                target = path / rel_path
                if target.exists():
                    target.unlink()
                deleted_paths.append(rel_text)

        self.events.emit(
            "worktree.merged",
            run_id=task["run_id"],
            task_id=task["id"],
            worktree_id=delivery_workspace["id"],
            path=str(path),
            merged_from=[item["id"] for item in worktrees],
            applied_count=len(applied_paths),
            conflict_count=len(set(conflicts)),
        )
        return delivery_workspace, {
            "applied_paths": sorted(set(applied_paths)),
            "deleted_paths": sorted(set(deleted_paths)),
            "conflicts": sorted(set(conflicts)),
        }

    def _sync_repo_snapshot(self, task: dict[str, Any], workspace_root: Path) -> int:
        copied = 0
        for source in sorted(self.repo_root.rglob("*")):
            rel = source.relative_to(self.repo_root)
            if self._should_skip_snapshot_path(rel):
                continue
            target = workspace_root / rel
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if source.is_symlink() or not source.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        self.events.emit(
            "worktree.snapshot_synced",
            run_id=task["run_id"],
            task_id=task["id"],
            path=str(workspace_root),
            file_count=copied,
        )
        return copied

    def _should_skip_snapshot_path(self, relative_path: Path) -> bool:
        return any(part in SNAPSHOT_EXCLUDED_NAMES for part in relative_path.parts)

    def _should_skip_delivery_path(self, relative_path: Path) -> bool:
        if self._should_skip_snapshot_path(relative_path):
            return True
        if relative_path.name in DELIVERY_META_FILES:
            return True
        return bool(relative_path.parts and relative_path.parts[0] == ".repopilot")

    def _copy_workspace_tree(self, source_root: Path, target_root: Path) -> int:
        copied = 0
        for source in sorted(source_root.rglob("*")):
            rel = source.relative_to(source_root)
            if self._should_skip_delivery_path(rel):
                continue
            target = target_root / rel
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if source.is_symlink() or not source.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        return copied

    def _workspace_changed_paths(self, workspace_root: Path) -> tuple[list[str], list[str]]:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return [], []
        changed_paths: list[str] = []
        deleted_paths: list[str] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.rstrip()
            if len(line) < 4:
                continue
            status = line[:2]
            path_text = line[3:]
            if "->" in path_text:
                path_text = path_text.split("->", 1)[1].strip()
            if "D" in status:
                deleted_paths.append(path_text)
                continue
            changed_paths.append(path_text)
        return changed_paths, deleted_paths

    def _has_head(self) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0

    def _run_git(self, args: list[str], cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stdout + result.stderr).strip() or f"git {' '.join(args)} failed")
        return (result.stdout + result.stderr).strip() or "(no output)"

    def _git_output(self, args: list[str], cwd: Path) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
        return (result.stdout + result.stderr).strip() or "(no output)"

    def _run_job(
        self,
        *,
        run_id: str,
        task_id: int,
        agent_id: str,
        command: str,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> dict[str, Any]:
        if any(fragment in command for fragment in DANGEROUS_FRAGMENTS):
            raise ValueError(f"Dangerous command blocked: {command}")
        job = self.jobs.create(
            {
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "command": command,
                "cwd": str(cwd),
                "output": "",
                "exit_code": None,
                "started_at": None,
                "finished_at": None,
            }
        )
        job["status"] = "running"
        job["started_at"] = now_ts()
        job["updated_at"] = now_ts()
        self.jobs.save(job)
        self.events.emit("job.started", run_id=run_id, task_id=task_id, job_id=job["id"], command=command)

        chunks: list[str] = []
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=merged_env,
        )

        try:
            stream = process.stdout
            if stream is None:
                raise RuntimeError("Unable to capture job output")
            deadline = time.time() + timeout
            while True:
                if time.time() > deadline:
                    process.kill()
                    raise subprocess.TimeoutExpired(command, timeout)
                ready, _, _ = select.select([stream], [], [], 0.2)
                if ready:
                    line = stream.readline()
                    if line:
                        chunks.append(line.rstrip("\n"))
                        output = "\n".join(chunks)[-50_000:]
                        job["output"] = output
                        job["updated_at"] = now_ts()
                        self.jobs.save(job)
                        self.events.emit(
                            "job.output",
                            run_id=run_id,
                            task_id=task_id,
                            job_id=job["id"],
                            line=line.rstrip("\n")[:2000],
                        )
                if process.poll() is not None:
                    tail = stream.read() or ""
                    if tail:
                        for line in tail.splitlines():
                            chunks.append(line)
                    break
            output = "\n".join(chunks).strip() or "(no output)"
            status = "completed" if process.returncode == 0 else "failed"
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            output = ("\n".join(chunks) + "\nError: command timed out").strip()
            status = "timeout"
            exit_code = None
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except Exception:
                pass

        job.update(
            {
                "status": status,
                "output": output[-50_000:],
                "exit_code": exit_code,
                "updated_at": now_ts(),
                "finished_at": now_ts(),
            }
        )
        self.jobs.save(job)
        self.events.emit(
            "job.finished",
            run_id=run_id,
            task_id=task_id,
            job_id=job["id"],
            command=command,
            status=status,
            exit_code=exit_code,
        )
        return self.jobs.get(job["id"])

    def _set_agent(self, run_id: str, agent_id: str, state: str, detail: str, task_id: int | None = None) -> None:
        run = self.get_run(run_id)
        agents = run.get("agents", {})
        agents[agent_id] = {
            "id": agent_id,
            "role": agent_id.split("-")[0],
            "state": state,
            "detail": detail,
            "task_id": task_id,
            "updated_at": now_ts(),
        }
        run["agents"] = agents
        run["updated_at"] = now_ts()
        self.runs.save(run)
        self.events.emit("agent.updated", run_id=run_id, agent_id=agent_id, state=state, detail=detail, task_id=task_id)

    def file_tree(self, worktree_id: str) -> list[dict[str, Any]]:
        worktree = self.worktrees.get(worktree_id)
        root = Path(worktree["path"])
        items: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if path.is_dir() or ".git" in path.parts:
                continue
            if path.stat().st_size > 256_000:
                continue
            items.append(
                {
                    "path": str(path.relative_to(root)),
                    "absolute_path": str(path),
                    "size": path.stat().st_size,
                }
            )
            if len(items) >= 400:
                break
        return items

    def read_file(self, path_text: str) -> dict[str, Any]:
        path = Path(path_text).expanduser().resolve()
        allowed_roots = [self.repo_root.resolve(), self.workspaces_root.resolve(), self.data_root.resolve()]
        if not any(is_relative_to(path, root) or path == root for root in allowed_roots):
            raise ValueError(f"Path is outside allowed roots: {path}")
        if not path.exists() or path.is_dir():
            raise ValueError(f"File not found: {path}")
        content = path.read_text(encoding="utf-8")
        return {"path": str(path), "content": content, "size": len(content)}


class HarnessManager:
    def __init__(self) -> None:
        self._runtimes: dict[str, RepoHarnessRuntime] = {}
        self._lock = threading.RLock()

    def runtime_for(self, repo_root: str | Path) -> RepoHarnessRuntime:
        root = detect_repo_root(Path(repo_root))
        key = str(root)
        with self._lock:
            if key not in self._runtimes:
                self._runtimes[key] = RepoHarnessRuntime(root)
            return self._runtimes[key]

    def all_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            runs.extend(runtime.list_runs())
        runs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return runs

    def find_runtime_by_run(self, run_id: str) -> RepoHarnessRuntime:
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            try:
                runtime.get_run(run_id)
                return runtime
            except Exception:
                continue
        raise ValueError(f"Unknown run: {run_id}")

    def find_runtime_by_task(self, task_id: int) -> RepoHarnessRuntime:
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            try:
                runtime.tasks.get(str(task_id))
                return runtime
            except Exception:
                continue
        raise ValueError(f"Unknown task: {task_id}")

    def find_runtime_by_worktree(self, worktree_id: str) -> RepoHarnessRuntime:
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            try:
                runtime.worktrees.get(worktree_id)
                return runtime
            except Exception:
                continue
        raise ValueError(f"Unknown worktree: {worktree_id}")

    def runtime_for_path(self, path_text: str) -> RepoHarnessRuntime:
        path = Path(path_text).expanduser().resolve()
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            allowed_roots = [runtime.repo_root.resolve(), runtime.workspaces_root.resolve(), runtime.data_root.resolve()]
            if any(is_relative_to(path, root) or path == root for root in allowed_roots):
                return runtime
        raise ValueError(f"No runtime registered for path: {path}")

    def default_repo_root(self) -> str:
        try:
            return str(detect_repo_root(Path.cwd()))
        except Exception:
            return str(Path.cwd().resolve())
