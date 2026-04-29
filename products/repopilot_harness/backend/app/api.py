from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .llm_client import llm_runtime_config
from .runtime import HarnessManager

DEFAULT_CODEX_COMMAND = (
    'codex exec --full-auto --dangerously-bypass-approvals-and-sandbox '
    '"$(cat "$REPOPILOT_PROMPT_FILE")"'
)


class RunCreateRequest(BaseModel):
    repo_root: str = Field(..., min_length=1)
    mission: str = Field(..., min_length=1)
    constraints: str = ""
    validation_commands: list[str] = Field(default_factory=list)
    execution_mode: str = ""
    agent_command: str = ""
    agent_timeout_seconds: int = 900


UI_DIR = Path(__file__).resolve().parent / "ui_static"


def resolve_execution_defaults() -> dict[str, object]:
    llm = llm_runtime_config()
    if llm["available"]:
        return {
            "default_execution_mode": "direct_llm",
            "default_agent_command": "",
            "default_agent_source": "direct_llm",
            "real_agent_available": True,
            "default_model_id": llm["model"],
            "default_base_url": llm["base_url"],
        }
    configured_command = os.getenv("REPOPILOT_AGENT_COMMAND", "").strip()
    if configured_command:
        return {
            "default_execution_mode": "agent_command",
            "default_agent_command": configured_command,
            "default_agent_source": "env",
            "real_agent_available": True,
            "default_model_id": "",
            "default_base_url": "",
        }
    if shutil.which("codex"):
        return {
            "default_execution_mode": "agent_command",
            "default_agent_command": DEFAULT_CODEX_COMMAND,
            "default_agent_source": "autodetect.codex",
            "real_agent_available": True,
            "default_model_id": "",
            "default_base_url": "",
        }
    return {
        "default_execution_mode": "scaffold",
        "default_agent_command": "",
        "default_agent_source": "none",
        "real_agent_available": False,
        "default_model_id": "",
        "default_base_url": "",
    }


def create_app(manager_instance: HarnessManager | None = None) -> FastAPI:
    manager = manager_instance or HarnessManager()
    app = FastAPI(title="RepoPilot Harness", version="0.3.0")
    app.state.manager = manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.get("/api/runs")
    def list_runs() -> dict:
        return {"items": manager.all_runs()}

    @app.get("/api/meta")
    def meta() -> dict:
        defaults = resolve_execution_defaults()
        return {
            "default_repo_root": manager.default_repo_root(),
            "default_execution_mode": defaults["default_execution_mode"],
            "default_agent_command": defaults["default_agent_command"],
            "default_agent_source": defaults["default_agent_source"],
            "real_agent_available": defaults["real_agent_available"],
            "default_agent_timeout_seconds": int(os.getenv("REPOPILOT_AGENT_TIMEOUT_SECONDS", "900")),
            "default_model_id": defaults["default_model_id"],
            "default_base_url": defaults["default_base_url"],
        }

    @app.post("/api/runs")
    def create_run(body: RunCreateRequest) -> dict:
        try:
            defaults = resolve_execution_defaults()
            requested_mode = body.execution_mode.strip() or str(defaults["default_execution_mode"])
            requested_command = body.agent_command.strip()
            if requested_mode == "direct_llm" and not llm_runtime_config()["available"]:
                raise ValueError(
                    "Direct LLM execution is not configured. Set ANTHROPIC_API_KEY and MODEL_ID first."
                )
            if requested_mode == "agent_command" and not requested_command:
                requested_command = str(defaults["default_agent_command"])
            if requested_mode == "agent_command" and not requested_command:
                raise ValueError(
                    "No real agent command is configured. Install `codex` or set REPOPILOT_AGENT_COMMAND."
                )
            runtime = manager.runtime_for(body.repo_root)
            run = runtime.create_run(
                mission=body.mission,
                constraints=body.constraints,
                validation_commands=body.validation_commands,
                execution_mode=requested_mode,
                agent_command=requested_command,
                agent_timeout_seconds=body.agent_timeout_seconds,
            )
            return run
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            runtime = manager.find_runtime_by_run(run_id)
            return runtime.snapshot(run_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/approve")
    def approve_run(run_id: str) -> dict:
        try:
            runtime = manager.find_runtime_by_run(run_id)
            return runtime.approve_run(run_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/pause")
    def pause_run(run_id: str) -> dict:
        try:
            runtime = manager.find_runtime_by_run(run_id)
            return runtime.pause_run(run_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/retry")
    def retry_task(task_id: int) -> dict:
        try:
            runtime = manager.find_runtime_by_task(task_id)
            return runtime.retry_task(task_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/worktrees/{worktree_id}/files")
    def list_worktree_files(worktree_id: str) -> dict:
        try:
            runtime = manager.find_runtime_by_worktree(worktree_id)
            return {"items": runtime.file_tree(worktree_id)}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/file")
    def read_file(path: str) -> dict:
        try:
            runtime = manager.runtime_for_path(path)
            return runtime.read_file(str(Path(path).resolve()))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/events")
    async def events_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        listeners: dict[str, tuple[object, object]] = {}
        try:
            await websocket.send_text(json.dumps({"type": "hello"}))
            while True:
                runs = manager.all_runs()
                current_ids = {run["id"] for run in runs[:12]}
                for run_id in list(listeners):
                    if run_id in current_ids:
                        continue
                    runtime, listener = listeners.pop(run_id)
                    runtime.events.unsubscribe(listener)
                for run in runs[:12]:
                    if run["id"] in listeners:
                        continue
                    runtime = manager.find_runtime_by_run(run["id"])
                    listeners[run["id"]] = (runtime, runtime.events.subscribe())
                if not listeners:
                    await asyncio.sleep(1.0)
                    continue
                delivered = False
                for run_id, (runtime, listener) in list(listeners.items()):
                    try:
                        event = await asyncio.to_thread(listener.get, True, 0.2)
                    except Exception:
                        continue
                    await websocket.send_text(json.dumps(event, ensure_ascii=True))
                    delivered = True
                if not delivered:
                    await asyncio.sleep(0.25)
        except WebSocketDisconnect:
            pass
        finally:
            for runtime, listener in listeners.values():
                runtime.events.unsubscribe(listener)

    return app
