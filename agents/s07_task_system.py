#!/usr/bin/env python3
"""s07_task_system.py - task tools with terminal-visible tracing."""

import argparse
import json
import os
import uuid
from pathlib import Path

try:
    import readline

    for binding in (
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ):
        try:
            readline.parse_and_bind(binding)
        except Exception:
            pass
except ImportError:
    pass

from agents.auth import make_client

TASKS_DIR = Path(".tasks")
client, MODEL = make_client()

TOOLS = [
    {
        "name": "create_task",
        "description": "Create a new task with a title and optional dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The task title"},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs this task depends on",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List all tasks.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_task",
        "description": "Get details of a specific task by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID (UUID)"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "update_task_status",
        "description": "Update the status of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID"},
                "status": {"type": "string", "description": "New status value"},
            },
            "required": ["task_id", "status"],
        },
    },
]

SYSTEM_PROMPT = (
    "You are a task management assistant. Use tools to create, list, get, and "
    "update tasks. Prefer using the tools instead of inventing task state."
)


def ensure_tasks_dir() -> None:
    TASKS_DIR.mkdir(exist_ok=True)


def task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def load_tasks() -> list[dict]:
    if not TASKS_DIR.exists():
        return []

    tasks = []
    for path in sorted(TASKS_DIR.glob("*.json")):
        raw_task = json.loads(path.read_text())
        tasks.append(
            {
                "id": str(raw_task.get("id", path.stem)),
                "title": raw_task.get("title")
                or raw_task.get("subject")
                or "(untitled)",
                "status": raw_task.get("status", "unknown"),
                "dependencies": raw_task.get("dependencies")
                or raw_task.get("blockedBy")
                or [],
                "raw": raw_task,
            }
        )
    return tasks


def render_task_snapshot() -> str:
    tasks = load_tasks()
    if not tasks:
        return "(no tasks)"

    lines = []
    for task in tasks:
        deps = ", ".join(str(dep) for dep in task.get("dependencies", [])) or "-"
        lines.append(
            f"- {task['id']} | {task['status']} | {task['title']} | deps: {deps}"
        )
    return "\n".join(lines)


def extract_text(blocks) -> str:
    text_parts = [block.text for block in blocks if getattr(block, "type", "") == "text"]
    return "\n".join(part for part in text_parts if part).strip()


def print_trace(message: str, verbose: bool) -> None:
    if verbose:
        print(message)


def print_task_snapshot(label: str, verbose: bool) -> None:
    if not verbose:
        return

    print(f"[tasks:{label}]")
    print(render_task_snapshot())


def handle_tool_call(tool_name: str, args: dict, verbose: bool = False) -> str:
    print_trace(
        f"[tool] {tool_name} {json.dumps(args, ensure_ascii=False, sort_keys=True)}",
        verbose,
    )
    print_task_snapshot("before", verbose)

    if tool_name == "create_task":
        ensure_tasks_dir()
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "title": args["title"],
            "status": "pending",
            "dependencies": args.get("dependencies", []),
        }
        task_path(task_id).write_text(json.dumps(task, indent=2))
        result = f"Task created with ID: {task_id}"

    elif tool_name == "list_tasks":
        tasks = load_tasks()
        result = (
            json.dumps([task["raw"] for task in tasks], indent=2)
            if tasks
            else "No tasks found."
        )

    elif tool_name == "get_task":
        try:
            result = task_path(args["task_id"]).read_text()
        except FileNotFoundError:
            result = f"Task {args['task_id']} not found"

    elif tool_name == "update_task_status":
        try:
            current_task = json.loads(task_path(args["task_id"]).read_text())
            current_task["status"] = args["status"]
            task_path(args["task_id"]).write_text(json.dumps(current_task, indent=2))
            result = (
                f"Task {args['task_id']} status updated to {args['status']}"
            )
        except FileNotFoundError:
            result = f"Task {args['task_id']} not found"

    else:
        result = f"Unknown tool: {tool_name}"

    print_trace(f"[tool_result] {result}", verbose)
    print_task_snapshot("after", verbose)
    return result


def parse_created_task_id(result: str) -> str:
    prefix = "Task created with ID:"
    if not result.startswith(prefix):
        raise ValueError(f"Unexpected create_task result: {result}")
    return result.removeprefix(prefix).strip()


def run_agent(prompt: str, messages=None, verbose: bool = False) -> str:
    if messages is None:
        messages = []

    messages.append({"role": "user", "content": prompt})
    round_no = 1

    while True:
        print_trace(f"[agent] round {round_no}: requesting model", verbose)
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        assistant_text = extract_text(response.content)
        if assistant_text:
            print_trace(f"[assistant] {assistant_text}", verbose)

        if response.stop_reason == "end_turn":
            print_trace("[agent] finished without more tool calls", verbose)
            return assistant_text

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            result = handle_tool_call(block.name, block.input, verbose=verbose)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})
        round_no += 1


def run_demo(verbose: bool = True) -> str:
    demo_title = f"s07-demo-{uuid.uuid4().hex[:8]}"
    print_trace("[demo] starting task lifecycle demo", verbose)

    print_trace(f"[demo] step 1/5 create_task title={demo_title}", verbose)
    create_result = handle_tool_call("create_task", {"title": demo_title}, verbose)
    task_id = parse_created_task_id(create_result)

    print_trace(f"[demo] step 2/5 list_tasks task_id={task_id}", verbose)
    handle_tool_call("list_tasks", {}, verbose)

    print_trace(f"[demo] step 3/5 get_task task_id={task_id}", verbose)
    handle_tool_call("get_task", {"task_id": task_id}, verbose)

    print_trace(
        f"[demo] step 4/5 update_task_status task_id={task_id} -> in_progress",
        verbose,
    )
    handle_tool_call(
        "update_task_status",
        {"task_id": task_id, "status": "in_progress"},
        verbose,
    )

    print_trace(f"[demo] step 5/5 list_tasks task_id={task_id}", verbose)
    handle_tool_call("list_tasks", {}, verbose)
    print_trace(f"[demo] finished task lifecycle demo for {task_id}", verbose)
    return task_id


def repl() -> None:
    history = []
    while True:
        try:
            prompt = input("\033[36ms07 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if prompt.strip().lower() in ("q", "exit", ""):
            break

        result = run_agent(prompt, messages=history, verbose=True)
        if result:
            print(result)
        print()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Lesson 7 task system with interactive and demo modes."
    )
    parser.add_argument(
        "-d",
        "--demo",
        action="store_true",
        help="Run a one-shot task lifecycle demo with terminal trace output.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.demo:
        run_demo(verbose=True)
        return
    repl()


if __name__ == "__main__":
    main()
