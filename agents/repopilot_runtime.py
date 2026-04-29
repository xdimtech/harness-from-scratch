from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def detect_repo_root(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip())
    return root if root.exists() else None


def is_dangerous_command(command: str) -> bool:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    return any(token in command for token in dangerous)


def slugify(text: str, fallback: str = "mission") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or fallback


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class EventHub:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        self._listeners: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "ts": time.time(),
            **payload,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(event)
        return event

    def list_events(self, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        items: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 2000)) :]:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if run_id and item.get("run_id") != run_id:
                continue
            items.append(item)
        return items

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        listener: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._listeners.add(listener)
        return listener

    def unsubscribe(self, listener: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._listeners.discard(listener)


class RunStore:
    def __init__(self, root: Path):
        self.dir = root / ".repopilot" / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def create(self, mission: str) -> dict[str, Any]:
        now = time.time()
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        slug = slugify(mission, fallback=run_id[-6:])
        run = {
            "id": run_id,
            "slug": slug,
            "mission": mission,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "agents": {},
            "task_ids": [],
            "summary_path": "",
        }
        self.save(run)
        return run

    def save(self, run: dict[str, Any]) -> None:
        run["updated_at"] = time.time()
        with self._lock:
            atomic_write_text(
                self._path(run["id"]),
                json.dumps(run, indent=2, ensure_ascii=True),
            )

    def get(self, run_id: str) -> dict[str, Any]:
        return json.loads(self._path(run_id).read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        runs = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.dir.glob("run_*.json"))
        ]
        runs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return runs


class TaskStore:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        ids: list[int] = []
        for file_path in self.dir.glob("task_*.json"):
            try:
                ids.append(int(file_path.stem.split("_")[1]))
            except Exception:
                pass
        return max(ids) if ids else 0

    def _path(self, task_id: int) -> Path:
        return self.dir / f"task_{task_id}.json"

    def save(self, task: dict[str, Any]) -> None:
        with self._lock:
            atomic_write_text(
                self._path(task["id"]),
                json.dumps(task, indent=2, ensure_ascii=True),
            )

    def create(
        self,
        run_id: str,
        subject: str,
        role: str,
        description: str = "",
        depends_on: list[int] | None = None,
    ) -> dict[str, Any]:
        task = {
            "id": self._next_id,
            "run_id": run_id,
            "subject": subject,
            "description": description,
            "role": role,
            "status": "pending",
            "owner": "",
            "worktree": "",
            "artifact_paths": [],
            "background_job_ids": [],
            "depends_on": depends_on or [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.save(task)
        self._next_id += 1
        return task

    def get(self, task_id: int) -> dict[str, Any]:
        return json.loads(self._path(task_id).read_text(encoding="utf-8"))

    def update(self, task_id: int, **changes: Any) -> dict[str, Any]:
        task = self.get(task_id)
        task.update(changes)
        task["updated_at"] = time.time()
        self.save(task)
        return task

    def list_tasks(self, run_id: str | None = None) -> list[dict[str, Any]]:
        tasks = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.dir.glob("task_*.json"))
        ]
        if run_id:
            tasks = [task for task in tasks if task.get("run_id") == run_id]
        tasks.sort(key=lambda item: item.get("created_at", 0))
        return tasks


class WorktreeManager:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(
                json.dumps({"worktrees": []}, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        self._lock = threading.Lock()

    def _load_index(self) -> dict[str, Any]:
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, data: dict[str, Any]) -> None:
        atomic_write_text(
            self.index_path,
            json.dumps(data, indent=2, ensure_ascii=True),
        )

    def list_all(self, run_id: str | None = None) -> list[dict[str, Any]]:
        items = self._load_index().get("worktrees", [])
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        return items

    def get(self, name: str) -> dict[str, Any] | None:
        for item in self._load_index().get("worktrees", []):
            if item.get("name") == name:
                return item
        return None

    def _run_git(self, args: list[str]) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            msg = (result.stdout + result.stderr).strip()
            raise RuntimeError(msg or f"git {' '.join(args)} failed")
        return (result.stdout + result.stderr).strip() or "(no output)"

    def _has_head(self) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0

    def create(self, name: str, run_id: str, task_id: int, owner: str) -> dict[str, Any]:
        path = self.dir / name
        branch = f"wt/{name}"
        if self._has_head():
            self._run_git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        else:
            self._run_git(["worktree", "add", "--orphan", "-b", branch, str(path)])
        entry = {
            "name": name,
            "path": str(path),
            "branch": branch,
            "run_id": run_id,
            "task_id": task_id,
            "owner": owner,
            "status": "active",
            "created_at": time.time(),
        }
        with self._lock:
            index = self._load_index()
            index.setdefault("worktrees", []).append(entry)
            self._save_index(index)
        return entry

    def mark(self, name: str, status: str) -> dict[str, Any]:
        with self._lock:
            index = self._load_index()
            updated: dict[str, Any] | None = None
            for item in index.get("worktrees", []):
                if item.get("name") == name:
                    item["status"] = status
                    item[f"{status}_at"] = time.time()
                    updated = item
                    break
            self._save_index(index)
        if not updated:
            raise ValueError(f"Unknown worktree {name}")
        return updated

    def run(self, name: str, command: str, timeout: int = 300) -> str:
        if is_dangerous_command(command):
            raise ValueError("Dangerous command blocked")
        worktree = self.get(name)
        if not worktree:
            raise ValueError(f"Unknown worktree {name}")
        result = subprocess.run(
            command,
            shell=True,
            cwd=worktree["path"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"

    def remove(self, name: str, force: bool = False) -> dict[str, Any]:
        worktree = self.get(name)
        if not worktree:
            raise ValueError(f"Unknown worktree {name}")
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(worktree["path"])
        self._run_git(args)
        return self.mark(name, "removed")


class BackgroundJobManager:
    def __init__(self, jobs_dir: Path, events: EventHub):
        self.dir = jobs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = events
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def _path(self, job_id: str) -> Path:
        return self.dir / f"job_{job_id}.json"

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write_text(
            self._path(data["id"]),
            json.dumps(data, indent=2, ensure_ascii=True),
        )

    def list_jobs(self, run_id: str | None = None) -> list[dict[str, Any]]:
        jobs = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.dir.glob("job_*.json"))
        ]
        if run_id:
            jobs = [job for job in jobs if job.get("run_id") == run_id]
        jobs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return jobs

    def get(self, job_id: str) -> dict[str, Any]:
        return json.loads(self._path(job_id).read_text(encoding="utf-8"))

    def run(
        self,
        *,
        run_id: str,
        task_id: int,
        agent: str,
        command: str,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if is_dangerous_command(command):
            raise ValueError("Dangerous command blocked")
        job_id = uuid.uuid4().hex[:8]
        job = {
            "id": job_id,
            "run_id": run_id,
            "task_id": task_id,
            "agent": agent,
            "command": command,
            "cwd": str(cwd),
            "status": "running",
            "result": "",
            "returncode": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._save(job)
        self.events.emit(
            "background.started",
            run_id=run_id,
            task_id=task_id,
            agent=agent,
            job_id=job_id,
            command=command,
        )
        thread = threading.Thread(
            target=self._execute,
            args=(job_id, cwd, env),
            daemon=True,
        )
        self._threads[job_id] = thread
        thread.start()
        return job

    def _execute(self, job_id: str, cwd: Path, env: dict[str, str] | None) -> None:
        job = self.get(job_id)
        try:
            result = subprocess.run(
                job["command"],
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
            output = (result.stdout + result.stderr).strip() or "(no output)"
            status = "completed" if result.returncode == 0 else "error"
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            output = "Error: Timeout (300s)"
            status = "timeout"
            returncode = None
        except Exception as exc:
            output = f"Error: {exc}"
            status = "error"
            returncode = None

        job["status"] = status
        job["result"] = output[:50000]
        job["returncode"] = returncode
        job["updated_at"] = time.time()
        self._save(job)
        self.events.emit(
            "background.finished",
            run_id=job["run_id"],
            task_id=job["task_id"],
            agent=job["agent"],
            job_id=job_id,
            status=status,
            returncode=returncode,
            command=job["command"],
        )

    def wait(self, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(job_id)
            if job["status"] != "running":
                return job
            time.sleep(0.1)
        return self.get(job_id)


class RepoPilotRuntime:
    AGENT_ROLES = {
        "lead": "Coordinator",
        "backend_dev": "Implementation",
        "qa_dev": "Validation",
        "docs_dev": "Documentation",
    }

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.events = EventHub(repo_root / ".repopilot" / "events.jsonl")
        self.runs = RunStore(repo_root)
        self.tasks = TaskStore(repo_root / ".tasks")
        self.worktrees = WorktreeManager(repo_root)
        self.jobs = BackgroundJobManager(repo_root / ".repopilot" / "jobs", self.events)
        self._threads: dict[str, threading.Thread] = {}

    def _summary_dir(self, run: dict[str, Any]) -> Path:
        path = self.repo_root / ".repopilot" / "summaries" / run["id"]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_run(self, run: dict[str, Any]) -> None:
        self.runs.save(run)

    def _set_agent(
        self,
        run: dict[str, Any],
        name: str,
        state: str,
        detail: str,
        task_id: int | None = None,
        worktree: str = "",
    ) -> None:
        current = self.runs.get(run["id"])
        current.setdefault("agents", {})
        current["agents"][name] = {
            "name": name,
            "role": self.AGENT_ROLES.get(name, name),
            "state": state,
            "detail": detail,
            "task_id": task_id,
            "worktree": worktree,
            "updated_at": time.time(),
        }
        run["agents"] = current["agents"]
        self._save_run(current)
        self.events.emit(
            "agent.updated",
            run_id=current["id"],
            agent=name,
            state=state,
            detail=detail,
            task_id=task_id,
            worktree=worktree,
        )

    def _create_task(
        self,
        run: dict[str, Any],
        subject: str,
        role: str,
        description: str,
    ) -> dict[str, Any]:
        task = self.tasks.create(run["id"], subject, role, description)
        run["task_ids"].append(task["id"])
        self._save_run(run)
        self.events.emit(
            "task.created",
            run_id=run["id"],
            task_id=task["id"],
            role=role,
            subject=subject,
        )
        return task

    def _update_task(self, run_id: str, task_id: int, **changes: Any) -> dict[str, Any]:
        task = self.tasks.update(task_id, **changes)
        self.events.emit(
            "task.updated",
            run_id=run_id,
            task_id=task_id,
            status=task.get("status"),
            owner=task.get("owner"),
            worktree=task.get("worktree"),
        )
        return task

    def start_mission(self, mission: str) -> dict[str, Any]:
        run = self.runs.create(mission)
        self.events.emit("run.created", run_id=run["id"], mission=mission, slug=run["slug"])
        thread = threading.Thread(target=self._execute_run, args=(run["id"],), daemon=True)
        self._threads[run["id"]] = thread
        thread.start()
        return run

    def _execute_run(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        mission = run["mission"]
        summary_dir = self._summary_dir(run)
        mission_file = summary_dir / "MISSION.md"
        mission_file.write_text(
            f"# Mission\n\n{mission}\n\nGenerated by RepoPilot Console.\n",
            encoding="utf-8",
        )
        run["summary_path"] = str(mission_file)
        self._save_run(run)

        self._set_agent(run, "lead", "planning", "Breaking mission into execution lanes")
        backend = self._create_task(
            run,
            "Create implementation scaffold",
            "backend_dev",
            "Write a Python implementation artifact in an isolated worktree.",
        )
        qa = self._create_task(
            run,
            "Create validation lane",
            "qa_dev",
            "Write a unittest-based contract check and execute it in background.",
        )
        docs = self._create_task(
            run,
            "Write operator docs",
            "docs_dev",
            "Produce README and release notes for the mission in a separate worktree.",
        )
        self.events.emit(
            "protocol.plan_proposed",
            run_id=run_id,
            agent="lead",
            payload={
                "tasks": [backend["id"], qa["id"], docs["id"]],
                "message": "Three isolated lanes: implementation, validation, documentation.",
            },
        )
        self._set_agent(run, "lead", "dispatching", "Spawning autonomous teammates")

        workers = [
            threading.Thread(target=self._run_backend_lane, args=(run_id, backend["id"]), daemon=True),
            threading.Thread(target=self._run_qa_lane, args=(run_id, qa["id"]), daemon=True),
            threading.Thread(target=self._run_docs_lane, args=(run_id, docs["id"]), daemon=True),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        run = self.runs.get(run_id)
        tasks = self.tasks.list_tasks(run_id)
        successful = all(task.get("status") == "completed" for task in tasks)
        self._set_agent(run, "lead", "reviewing", "Collecting artifacts from each lane")
        self.events.emit(
            "protocol.review_request",
            run_id=run_id,
            agent="lead",
            payload={
                "message": "Reviewing backend, QA, and docs artifacts before sign-off.",
                "task_ids": run["task_ids"],
            },
        )
        run["status"] = "completed" if successful else "failed"
        self._save_run(run)
        summary = self._write_summary(run)
        decision = "approved" if successful else "changes_requested"
        self.events.emit(
            "protocol.review_response",
            run_id=run_id,
            agent="lead",
            payload={"decision": decision, "summary_path": str(summary)},
        )
        run["summary_path"] = str(summary)
        self._save_run(run)
        if successful:
            self._set_agent(run, "lead", "complete", "Mission completed; artifacts ready for inspection")
            self.events.emit("run.completed", run_id=run_id, summary_path=str(summary))
        else:
            blocked = [task["id"] for task in tasks if task.get("status") != "completed"]
            self._set_agent(run, "lead", "error", "Mission requires attention; one or more lanes failed")
            self.events.emit(
                "run.failed",
                run_id=run_id,
                summary_path=str(summary),
                blocked_task_ids=blocked,
            )

    def _task_output_base(self, run: dict[str, Any], lane: str) -> str:
        return f"repopilot_runs/{run['slug']}/{lane}"

    def _build_worktree_name(self, run: dict[str, Any], owner: str, task_id: int) -> str:
        slug = slugify(run["slug"], fallback="mission")[:24]
        run_suffix = run["id"].split("_")[-1][:6]
        owner_slug = slugify(owner, fallback="agent")[:12]
        return f"{slug}-{run_suffix}-{owner_slug}-{task_id}"

    def _claim_task(self, run: dict[str, Any], task_id: int, owner: str) -> tuple[dict[str, Any], dict[str, Any]]:
        task = self._update_task(run["id"], task_id, status="in_progress", owner=owner)
        worktree_name = self._build_worktree_name(run, owner, task_id)
        self.events.emit(
            "task.claimed",
            run_id=run["id"],
            task_id=task_id,
            agent=owner,
        )
        self.events.emit(
            "worktree.create.before",
            run_id=run["id"],
            task_id=task_id,
            agent=owner,
            worktree=worktree_name,
        )
        wt = self.worktrees.create(worktree_name, run["id"], task_id, owner)
        self.events.emit(
            "worktree.create.after",
            run_id=run["id"],
            task_id=task_id,
            agent=owner,
            worktree=wt,
        )
        task = self._update_task(run["id"], task_id, worktree=worktree_name)
        return task, wt

    def _mark_lane_failed(
        self,
        run_id: str,
        task_id: int,
        agent: str,
        message: str,
        exc: Exception,
        worktree_name: str = "",
    ) -> None:
        detail = f"{message}: {exc}"
        self._update_task(run_id, task_id, status="blocked", error=detail)
        run = self.runs.get(run_id)
        self._set_agent(run, agent, "error", detail, task_id, worktree_name)
        self.events.emit(
            "lane.failed",
            run_id=run_id,
            task_id=task_id,
            agent=agent,
            worktree=worktree_name,
            error=detail,
        )

    def _run_backend_lane(self, run_id: str, task_id: int) -> None:
        worktree_name = ""
        try:
            run = self.runs.get(run_id)
            self._set_agent(run, "backend_dev", "claiming", "Taking implementation lane", task_id)
            task, wt = self._claim_task(run, task_id, "backend_dev")
            worktree_name = wt["name"]
            self._set_agent(run, "backend_dev", "coding", "Writing implementation scaffold", task_id, wt["name"])
            base = self._task_output_base(run, "backend")
            Path(wt["path"], base).mkdir(parents=True, exist_ok=True)
            feature = Path(wt["path"], base, "feature.py")
            init_file = Path(wt["path"], base, "__init__.py")
            contents = (
                '"""Backend lane artifact for RepoPilot mission demo."""\n\n'
                f'MISSION = {run["mission"]!r}\n\n'
                'def build_feature_summary() -> dict[str, str]:\n'
                '    return {\n'
                '        "lane": "backend_dev",\n'
                '        "status": "prototype-ready",\n'
                '        "mission": MISSION,\n'
                '    }\n'
            )
            init_file.write_text("from .feature import build_feature_summary\n", encoding="utf-8")
            feature.write_text(contents, encoding="utf-8")
            artifacts = [str(init_file.relative_to(wt["path"])), str(feature.relative_to(wt["path"]))]
            job = self.jobs.run(
                run_id=run_id,
                task_id=task_id,
                agent="backend_dev",
                command=f"python3 -m py_compile {base}/__init__.py {base}/feature.py",
                cwd=Path(wt["path"]),
            )
            self._update_task(run_id, task_id, artifact_paths=artifacts, background_job_ids=[job["id"]])
            final_job = self.jobs.wait(job["id"], timeout=30)
            self.events.emit(
                "artifact.ready",
                run_id=run_id,
                task_id=task_id,
                agent="backend_dev",
                paths=artifacts,
                worktree=wt["name"],
            )
            if final_job["status"] != "completed":
                raise RuntimeError(f"background job {job['id']} finished with status={final_job['status']}")
            self._update_task(run_id, task_id, status="completed")
            self._set_agent(run, "backend_dev", "complete", "Implementation artifact ready", task_id, wt["name"])
            self.events.emit(
                "protocol.review_request",
                run_id=run_id,
                agent="backend_dev",
                payload={"task_id": task_id, "message": "Implementation scaffold ready for inspection."},
            )
        except Exception as exc:
            self._mark_lane_failed(run_id, task_id, "backend_dev", "Backend lane failed", exc, worktree_name)

    def _run_qa_lane(self, run_id: str, task_id: int) -> None:
        worktree_name = ""
        try:
            run = self.runs.get(run_id)
            self._set_agent(run, "qa_dev", "claiming", "Taking validation lane", task_id)
            task, wt = self._claim_task(run, task_id, "qa_dev")
            worktree_name = wt["name"]
            self._set_agent(run, "qa_dev", "testing", "Writing and executing contract tests", task_id, wt["name"])
            base = self._task_output_base(run, "qa")
            target = Path(wt["path"], base)
            target.mkdir(parents=True, exist_ok=True)
            contract = target / "contract.py"
            test_file = target / "test_contract.py"
            contract.write_text(
                (
                    'def mission_contract() -> dict[str, str]:\n'
                    '    return {\n'
                    f'        "mission": {run["mission"]!r},\n'
                    '        "owner": "qa_dev",\n'
                    '        "verdict": "green",\n'
                    '    }\n'
                ),
                encoding="utf-8",
            )
            test_file.write_text(
                (
                    'import unittest\n\n'
                    'from contract import mission_contract\n\n'
                    'class MissionContractTest(unittest.TestCase):\n'
                    '    def test_contract_contains_mission(self):\n'
                    '        payload = mission_contract()\n'
                    '        self.assertIn("mission", payload)\n'
                    '        self.assertEqual(payload["verdict"], "green")\n\n'
                    'if __name__ == "__main__":\n'
                    '    unittest.main()\n'
                ),
                encoding="utf-8",
            )
            artifacts = [str(contract.relative_to(wt["path"])), str(test_file.relative_to(wt["path"]))]
            command = (
                f"PYTHONPATH={base} python3 -m unittest discover -s {base} -p 'test_*.py' -v"
            )
            job = self.jobs.run(
                run_id=run_id,
                task_id=task_id,
                agent="qa_dev",
                command=command,
                cwd=Path(wt["path"]),
            )
            self._update_task(run_id, task_id, artifact_paths=artifacts, background_job_ids=[job["id"]])
            final_job = self.jobs.wait(job["id"], timeout=30)
            self.events.emit(
                "artifact.ready",
                run_id=run_id,
                task_id=task_id,
                agent="qa_dev",
                paths=artifacts,
                worktree=wt["name"],
            )
            if final_job["status"] != "completed":
                raise RuntimeError(f"background job {job['id']} finished with status={final_job['status']}")
            self._update_task(run_id, task_id, status="completed")
            self._set_agent(run, "qa_dev", "complete", "Validation lane finished", task_id, wt["name"])
        except Exception as exc:
            self._mark_lane_failed(run_id, task_id, "qa_dev", "QA lane failed", exc, worktree_name)

    def _run_docs_lane(self, run_id: str, task_id: int) -> None:
        worktree_name = ""
        try:
            run = self.runs.get(run_id)
            self._set_agent(run, "docs_dev", "claiming", "Taking docs lane", task_id)
            task, wt = self._claim_task(run, task_id, "docs_dev")
            worktree_name = wt["name"]
            self._set_agent(run, "docs_dev", "writing", "Producing README and release note", task_id, wt["name"])
            base = self._task_output_base(run, "docs")
            target = Path(wt["path"], base)
            target.mkdir(parents=True, exist_ok=True)
            readme = target / "README.md"
            changelog = target / "CHANGELOG.md"
            readme.write_text(
                (
                    f"# RepoPilot Mission Demo\n\n"
                    f"Mission: {run['mission']}\n\n"
                    "This lane documents the operator-facing story of the run.\n"
                ),
                encoding="utf-8",
            )
            changelog.write_text(
                (
                    "# Release Notes\n\n"
                    "- Documented the mission intent.\n"
                    "- Captured the isolated worktree output for docs review.\n"
                ),
                encoding="utf-8",
            )
            artifacts = [str(readme.relative_to(wt["path"])), str(changelog.relative_to(wt["path"]))]
            self._update_task(run_id, task_id, artifact_paths=artifacts)
            self.events.emit(
                "artifact.ready",
                run_id=run_id,
                task_id=task_id,
                agent="docs_dev",
                paths=artifacts,
                worktree=wt["name"],
            )
            self._update_task(run_id, task_id, status="completed")
            self._set_agent(run, "docs_dev", "complete", "Docs lane finished", task_id, wt["name"])
        except Exception as exc:
            self._mark_lane_failed(run_id, task_id, "docs_dev", "Docs lane failed", exc, worktree_name)

    def _write_summary(self, run: dict[str, Any]) -> Path:
        summary_dir = self._summary_dir(run)
        summary = summary_dir / "RUN_SUMMARY.md"
        tasks = self.tasks.list_tasks(run["id"])
        lines = [
            f"# Run Summary: {run['id']}",
            "",
            f"Mission: {run['mission']}",
            "",
            f"Run status: {run.get('status', 'running')}",
            "",
            "## Tasks",
            "",
        ]
        for task in tasks:
            lines.append(f"- #{task['id']} [{task['status']}] {task['subject']} owner={task['owner']} wt={task['worktree']}")
            if task.get("error"):
                lines.append(f"  - error: {task['error']}")
            for artifact in task.get("artifact_paths", []):
                lines.append(f"  - {artifact}")
        summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary

    def wait_for_run(self, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.runs.get(run_id)
            if run.get("status") != "running":
                return run
            time.sleep(0.2)
        return self.runs.get(run_id)

    def read_file(self, path_text: str) -> dict[str, Any]:
        path = Path(path_text).resolve()
        if not path.is_relative_to(self.repo_root.resolve()):
            raise ValueError("Path escapes repo root")
        text = path.read_text(encoding="utf-8")
        return {
            "path": str(path),
            "size": len(text),
            "content": text[:200000],
        }

    def file_tree(self, worktree_name: str, limit: int = 100) -> list[dict[str, Any]]:
        worktree = self.worktrees.get(worktree_name)
        if not worktree:
            raise ValueError(f"Unknown worktree {worktree_name}")
        root = Path(worktree["path"])
        run = self.runs.get(worktree["run_id"])
        preferred_root = root / "repopilot_runs" / run["slug"]
        scan_root = preferred_root if preferred_root.exists() else root
        entries: list[dict[str, Any]] = []
        for path in sorted(scan_root.rglob("*")):
            if ".git" in path.parts:
                continue
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            entries.append({"path": str(rel), "size": path.stat().st_size})
            if len(entries) >= limit:
                break
        return entries

    def state(self, run_id: str | None = None) -> dict[str, Any]:
        runs = self.runs.list_runs()
        active_run = run_id or (runs[0]["id"] if runs else None)
        current = self.runs.get(active_run) if active_run else None
        tasks = self.tasks.list_tasks(active_run) if active_run else []
        worktrees = self.worktrees.list_all(active_run) if active_run else []
        jobs = self.jobs.list_jobs(active_run) if active_run else []
        events = self.events.list_events(active_run, limit=200) if active_run else []
        return {
            "runs": runs,
            "current_run": current,
            "tasks": tasks,
            "worktrees": worktrees,
            "jobs": jobs,
            "events": events,
        }


DEFAULT_RUNTIME = RepoPilotRuntime(detect_repo_root(Path.cwd()) or Path.cwd())
