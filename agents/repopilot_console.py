from __future__ import annotations

import asyncio
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from agents.repopilot_runtime import DEFAULT_RUNTIME, RepoPilotRuntime
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agents.repopilot_runtime import DEFAULT_RUNTIME, RepoPilotRuntime


class MissionRequest(BaseModel):
    mission: str


APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "repopilot_ui"
app = FastAPI(title="RepoPilot Console", version="0.1.0")
runtime: RepoPilotRuntime = DEFAULT_RUNTIME

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


@app.get("/api/state")
def get_state(run_id: str | None = None) -> dict:
    return runtime.state(run_id)


@app.post("/api/missions")
def create_mission(body: MissionRequest) -> dict:
    mission = body.mission.strip()
    if not mission:
        raise HTTPException(status_code=400, detail="mission is required")
    return runtime.start_mission(mission)


@app.get("/api/worktrees/{name}/files")
def list_worktree_files(name: str) -> dict:
    try:
        return {"items": runtime.file_tree(name)}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/file")
def read_file(path: str) -> dict:
    try:
        return runtime.read_file(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/worktrees/{name}/keep")
def keep_worktree(name: str) -> dict:
    try:
        worktree = runtime.worktrees.mark(name, "kept")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.events.emit(
        "worktree.keep",
        run_id=worktree.get("run_id"),
        task_id=worktree.get("task_id"),
        agent=worktree.get("owner"),
        worktree=worktree,
    )
    return worktree


@app.delete("/api/worktrees/{name}")
def remove_worktree(name: str) -> dict:
    try:
        worktree = runtime.worktrees.remove(name, force=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.events.emit(
        "worktree.remove.after",
        run_id=worktree.get("run_id"),
        task_id=worktree.get("task_id"),
        agent=worktree.get("owner"),
        worktree=worktree,
    )
    return worktree


@app.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    listener = runtime.events.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "hello"}))
        while True:
            event = await asyncio.to_thread(listener.get)
            await websocket.send_text(json.dumps(event, ensure_ascii=True))
    except WebSocketDisconnect:
        runtime.events.unsubscribe(listener)
    except Exception:
        runtime.events.unsubscribe(listener)
        raise


def main() -> None:
    uvicorn.run("agents.repopilot_console:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
