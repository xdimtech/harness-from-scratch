#!/usr/bin/env python3
"""s06_context_compact.py - three-layer context compaction."""

import json
import os
import subprocess
import time
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

WORKDIR = Path.cwd()
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
THRESHOLD = int(os.getenv("CONTEXT_COMPACT_THRESHOLD", "50000"))
KEEP_RECENT = 3
PRESERVE_RESULT_TOOLS = {"read_file"}

client, MODEL = make_client()

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use tools to inspect and modify the workspace.
If the conversation gets too long or cluttered, use the compact tool to compress it while preserving continuity.
"""


def estimate_tokens(messages: list) -> int:
    """Use a cheap character heuristic instead of another model call."""
    return len(json.dumps(messages, default=str, ensure_ascii=False)) // 4


def safe_path(path_text: str) -> Path:
    path = (WORKDIR / path_text).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_text}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(token in command for token in dangerous):
        return "Error: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as exc:
        return f"Error: {exc}"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"


def build_tool_name_map(messages: list) -> dict[str, str]:
    tool_name_map: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_name_map[block.id] = block.name
    return tool_name_map


def micro_compact(messages: list) -> list:
    """Replace older verbose tool results with placeholders."""
    tool_results: list[dict] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_result":
                tool_results.append(part)

    if len(tool_results) <= KEEP_RECENT:
        return messages

    tool_name_map = build_tool_name_map(messages)
    for result in tool_results[:-KEEP_RECENT]:
        content = result.get("content")
        if not isinstance(content, str) or len(content) <= 100:
            continue

        tool_name = tool_name_map.get(result.get("tool_use_id", ""), "unknown")
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        if content.startswith("[Previous: used "):
            continue

        result["content"] = f"[Previous: used {tool_name}]"
        print(f"[micro_compact] replaced old result from {tool_name}")

    return messages


def save_transcript(messages: list) -> Path:
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with transcript_path.open("w") as handle:
        for message in messages:
            handle.write(json.dumps(message, default=str, ensure_ascii=False) + "\n")
    print(f"[transcript saved: {transcript_path}]")
    return transcript_path


def auto_compact(messages: list, focus: str | None = None, reason: str = "auto") -> list:
    transcript_path = save_transcript(messages)
    conversation_text = json.dumps(
        messages, default=str, ensure_ascii=False
    )[-80000:]

    focus_hint = ""
    if focus:
        focus_hint = f"\nPay extra attention to preserving: {focus}"

    response = client.messages.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize this conversation for continuity. Include: "
                    "1) what was accomplished, 2) current state, 3) important files, "
                    "4) open questions or next steps. Be concise but preserve critical details."
                    f"{focus_hint}\n\n"
                    f"Conversation:\n{conversation_text}"
                ),
            }
        ],
        max_tokens=2000,
    )
    summary = "".join(
        block.text for block in response.content if hasattr(block, "text")
    ).strip()
    if not summary:
        summary = "No summary generated."

    print(f"[{reason}_compact] conversation replaced with summary")
    return [
        {
            "role": "user",
            "content": (
                f"[Conversation compressed via {reason}. "
                f"Transcript: {transcript_path}]\n\n{summary}"
            ),
        }
    ]


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(
        kw["path"], kw["old_text"], kw["new_text"]
    ),
}

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "compact",
        "description": "Compress the current conversation and preserve continuity in a shorter summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Critical details to preserve in the compressed summary.",
                }
            },
        },
    },
]


def agent_loop(messages: list) -> None:
    while True:
        micro_compact(messages)

        estimated_tokens = estimate_tokens(messages)
        if estimated_tokens > THRESHOLD:
            print(
                f"[auto_compact triggered] estimated_tokens={estimated_tokens} "
                f"threshold={THRESHOLD}"
            )
            messages[:] = auto_compact(messages, reason="auto")

        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return

        results = []
        manual_focus = None
        manual_compact = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "compact":
                manual_compact = True
                manual_focus = block.input.get("focus")
                output = "Manual compression requested."
            else:
                handler = TOOL_HANDLERS.get(block.name)
                output = (
                    handler(**block.input)
                    if handler
                    else f"Unknown tool: {block.name}"
                )

            print(f"> {block.name}:")
            print(f"  {str(output)[:200]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )

        messages.append({"role": "user", "content": results})

        if manual_compact:
            print("[manual compact]")
            messages[:] = auto_compact(
                messages,
                focus=manual_focus,
                reason="manual",
            )


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        agent_loop(history)

        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        elif isinstance(response_content, str):
            print(response_content)
        print()
