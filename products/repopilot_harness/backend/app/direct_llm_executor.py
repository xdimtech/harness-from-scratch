from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm_client import AnthropicLLMClient, LLMClient, create_llm_client, llm_runtime_config
from .util import atomic_write_text, now_ts, safe_resolve, summarize_repo

MAX_TOOL_OUTPUT_CHARS = 16_000
DEFAULT_MAX_STEPS = 18


@dataclass(slots=True)
class DirectLLMExecutionResult:
    summary: str
    artifact_paths: list[str]
    step_count: int


class TaskFinished(Exception):
    def __init__(self, summary: str):
        super().__init__(summary)
        self.summary = summary


class WorkspaceToolbox:
    def __init__(
        self,
        *,
        runtime: Any,
        run: dict[str, Any],
        task: dict[str, Any],
        agent_id: str,
        worktree: dict[str, Any],
        artifact_dir: Path,
    ):
        self.runtime = runtime
        self.run = run
        self.task = task
        self.agent_id = agent_id
        self.worktree = worktree
        self.workspace_root = Path(worktree["path"]).resolve()
        self.artifact_dir = artifact_dir
        self.tool_call_count = 0
        self.mutation_count = 0
        self.finished_summary = ""

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_files",
                "description": "List files under a workspace-relative path. Use to inspect project structure before editing.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "search_files",
                "description": "Search text in files with ripgrep. Returns matching file names and line numbers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_file",
                "description": "Read a text file from the workspace with line numbers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": (
                    "Write or replace a small text file in the workspace. "
                    "If the content is long, prefer run_command with a heredoc or use smaller edit_file steps."
                ),
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
                "description": "Replace exact text inside a file. Use for targeted edits.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "run_command",
                "description": (
                    "Run a shell command inside the current workspace and capture stdout/stderr. "
                    "You can use this to create or rewrite larger files via cat <<'EOF' when tool inputs would be too large."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "finish_task",
                "description": "Call this only when the coding task is actually complete. Summarize what changed, verification done, and any residual risk.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                    },
                    "required": ["summary"],
                },
            },
        ]

    def execute(self, name: str, payload: dict[str, Any]) -> str:
        self.tool_call_count += 1
        self.runtime.events.emit(
            "tool.called",
            run_id=self.run["id"],
            task_id=self.task["id"],
            agent_id=self.agent_id,
            tool_name=name,
            payload=json.dumps(payload, ensure_ascii=True)[:4000],
        )
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        if name == "write_file" and "content" not in payload:
            raise ValueError(
                "write_file requires both path and content. If your file content is large, "
                "use run_command with a heredoc like: cat > path <<'EOF' ... EOF"
            )
        if name == "edit_file" and ("old_text" not in payload or "new_text" not in payload):
            raise ValueError("edit_file requires path, old_text, and new_text")
        result = str(handler(**payload))
        trimmed = self._trim(result)
        self.runtime.events.emit(
            "tool.result",
            run_id=self.run["id"],
            task_id=self.task["id"],
            agent_id=self.agent_id,
            tool_name=name,
            detail=trimmed[:2000],
        )
        return trimmed

    def tool_list_files(self, path: str = ".", glob: str = "**/*", limit: int = 120) -> str:
        target = self._resolve_path(path)
        entries: list[str] = []
        if target.is_file():
            return f"FILE {target.relative_to(self.workspace_root)} ({target.stat().st_size} bytes)"
        for item in sorted(target.glob(glob)):
            if len(entries) >= max(1, min(int(limit or 120), 300)):
                break
            if ".git" in item.parts or item.is_dir():
                continue
            rel = item.relative_to(self.workspace_root)
            entries.append(f"{rel} ({item.stat().st_size} bytes)")
        return "\n".join(entries) if entries else f"No files found under {target.relative_to(self.workspace_root)}"

    def tool_search_files(self, query: str, path: str = ".", limit: int = 80) -> str:
        if not query.strip():
            raise ValueError("query is required")
        target = self._resolve_path(path)
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", "--max-count", str(max(1, min(int(limit or 80), 200))), query, str(target)]
        try:
            result = subprocess.run(cmd, cwd=self.workspace_root, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            fallback = subprocess.run(
                ["grep", "-RIn", query, str(target)],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (fallback.stdout or fallback.stderr).strip()
            return output or f"No matches for {query!r}"
        output = (result.stdout or result.stderr).strip()
        return output or f"No matches for {query!r}"

    def tool_read_file(self, path: str, start_line: int = 1, end_line: int = 200) -> str:
        target = self._resolve_path(path)
        content = target.read_text(encoding="utf-8")
        start = max(1, int(start_line or 1))
        end = max(start, min(int(end_line or 200), start + 399))
        lines = content.splitlines()
        selected = lines[start - 1 : end]
        numbered = [f"{idx}: {line}" for idx, line in enumerate(selected, start=start)]
        return f"# {target.relative_to(self.workspace_root)}\n" + "\n".join(numbered)

    def tool_write_file(self, path: str, content: str) -> str:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.mutation_count += 1
        return f"Wrote {target.relative_to(self.workspace_root)} ({len(content)} chars)"

    def tool_edit_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        target = self._resolve_path(path)
        content = target.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError(f"old_text not found in {path}")
        if occurrences > 1 and not replace_all:
            raise ValueError(f"old_text appears {occurrences} times in {path}; set replace_all=true or choose a more specific snippet")
        updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        self.mutation_count += 1
        replaced = occurrences if replace_all else 1
        return f"Updated {target.relative_to(self.workspace_root)} with {replaced} replacement(s)"

    def tool_run_command(self, command: str, timeout_seconds: int = 180) -> str:
        before = self._workspace_fingerprint()
        job = self.runtime._run_job(
            run_id=self.run["id"],
            task_id=self.task["id"],
            agent_id=self.agent_id,
            command=command,
            cwd=self.workspace_root,
            env={
                "REPOPILOT_TASK_ID": str(self.task["id"]),
                "REPOPILOT_TASK_TITLE": self.task["title"],
                "REPOPILOT_MISSION": self.run["mission"],
                "REPOPILOT_WORKTREE": str(self.workspace_root),
            },
            timeout=max(30, min(int(timeout_seconds or 180), 1800)),
        )
        after = self._workspace_fingerprint()
        if after != before:
            self.mutation_count += 1
        if job["status"] != "completed":
            raise ValueError(
                f"Command failed with status={job['status']} exit_code={job['exit_code']}\n{job['output']}"
            )
        return f"status={job['status']} exit_code={job['exit_code']}\n{job['output']}"

    def tool_finish_task(self, summary: str) -> str:
        self.finished_summary = summary.strip()
        raise TaskFinished(self.finished_summary or "Task finished")

    def _resolve_path(self, path: str) -> Path:
        candidate = self.workspace_root / path
        return safe_resolve(self.workspace_root, str(candidate))

    def _trim(self, text: str) -> str:
        if len(text) <= MAX_TOOL_OUTPUT_CHARS:
            return text
        return text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated by RepoPilot]"

    def _workspace_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        items: list[tuple[str, int, int]] = []
        for path in sorted(self.workspace_root.rglob("*")):
            if ".git" in path.parts or path.is_dir():
                continue
            rel = str(path.relative_to(self.workspace_root))
            stat = path.stat()
            items.append((rel, stat.st_size, stat.st_mtime_ns))
        return tuple(items)


class DirectLLMExecutor:
    def __init__(self, client: LLMClient | None = None):
        self.client = client or create_llm_client()
        self.config = llm_runtime_config()

    def execute(
        self,
        *,
        runtime: Any,
        run: dict[str, Any],
        task: dict[str, Any],
        agent_id: str,
        worktree: dict[str, Any],
        artifact_dir: Path,
        prompt_path: Path,
        task_brief_path: Path,
        notes_path: Path,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> DirectLLMExecutionResult:
        toolbox = WorkspaceToolbox(
            runtime=runtime,
            run=run,
            task=task,
            agent_id=agent_id,
            worktree=worktree,
            artifact_dir=artifact_dir,
        )
        trace_path = artifact_dir / "agent_trace.jsonl"
        delivery_path = artifact_dir / "agent_delivery.md"
        repo_profile = summarize_repo(Path(worktree["path"]))
        native_tools = not self.config.get("uses_proxy", False) or not isinstance(self.client, AnthropicLLMClient)
        system_prompt = self._build_system_prompt(task=task, run=run, worktree=worktree, native_tools=native_tools)
        user_prompt = self._build_user_prompt(
            run=run,
            task=task,
            worktree=worktree,
            prompt_path=prompt_path,
            task_brief_path=task_brief_path,
            notes_path=notes_path,
            repo_profile=repo_profile,
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        trace_lines: list[str] = []
        if native_tools:
            final_summary = self._execute_with_native_tools(
                runtime=runtime,
                run=run,
                task=task,
                agent_id=agent_id,
                toolbox=toolbox,
                system_prompt=system_prompt,
                messages=messages,
                trace_lines=trace_lines,
                max_steps=max_steps,
            )
        else:
            final_summary = self._execute_with_text_actions(
                runtime=runtime,
                run=run,
                task=task,
                agent_id=agent_id,
                toolbox=toolbox,
                system_prompt=system_prompt,
                messages=messages,
                trace_lines=trace_lines,
                max_steps=max_steps,
            )

        if not final_summary:
            raise RuntimeError("LLM did not finish with a delivery summary")
        if toolbox.mutation_count == 0:
            raise RuntimeError("Direct LLM execution ended without any write_file/edit_file mutation")

        atomic_write_text(trace_path, "\n".join(trace_lines) + ("\n" if trace_lines else ""))
        diff_stat = runtime._git_output(["status", "--short"], cwd=Path(worktree["path"]))
        atomic_write_text(
            delivery_path,
            (
                "# Direct LLM Delivery\n\n"
                f"Task: {task['title']}\n\n"
                f"Model: {self.config.get('model', '(unknown)')}\n\n"
                f"Workspace: `{worktree['path']}`\n\n"
                f"Steps used: {len(trace_lines)}\n\n"
                "## Summary\n"
                f"{final_summary}\n\n"
                "## Workspace status\n\n"
                "```text\n"
                f"{diff_stat}\n"
                "```\n"
            ),
        )
        return DirectLLMExecutionResult(
            summary=final_summary,
            artifact_paths=[str(trace_path), str(delivery_path)],
            step_count=len(trace_lines),
        )

    def _execute_with_native_tools(
        self,
        *,
        runtime: Any,
        run: dict[str, Any],
        task: dict[str, Any],
        agent_id: str,
        toolbox: WorkspaceToolbox,
        system_prompt: str,
        messages: list[dict[str, Any]],
        trace_lines: list[str],
        max_steps: int,
    ) -> str:
        final_summary = ""
        step_limit = max(4, min(int(max_steps or DEFAULT_MAX_STEPS), 40))
        for step in range(1, step_limit + 1):
            runtime.events.emit(
                "llm.turn.started",
                run_id=run["id"],
                task_id=task["id"],
                agent_id=agent_id,
                step=step,
                model=self.config.get("model", ""),
            )
            turn = self.client.complete(
                system=system_prompt,
                messages=messages,
                tools=toolbox.tool_schemas(),
                max_tokens=2400,
            )
            messages.append({"role": "assistant", "content": turn.content_blocks})
            trace_lines.append(
                json.dumps(
                    {
                        "ts": now_ts(),
                        "mode": "native_tools",
                        "step": step,
                        "assistant_text": turn.text,
                        "stop_reason": turn.stop_reason,
                        "tool_uses": [{"id": item.id, "name": item.name, "input": item.input} for item in turn.tool_uses],
                    },
                    ensure_ascii=True,
                )
            )
            if turn.text:
                runtime.events.emit(
                    "llm.turn.text",
                    run_id=run["id"],
                    task_id=task["id"],
                    agent_id=agent_id,
                    step=step,
                    detail=turn.text[:2000],
                )
            runtime.events.emit(
                "llm.turn.finished",
                run_id=run["id"],
                task_id=task["id"],
                agent_id=agent_id,
                step=step,
                stop_reason=turn.stop_reason,
                tool_use_count=len(turn.tool_uses),
            )

            if not turn.tool_uses:
                if turn.text.strip():
                    return turn.text.strip()
                raise RuntimeError("LLM stopped without any tool call or completion summary")

            tool_results: list[dict[str, Any]] = []
            try:
                for item in turn.tool_uses:
                    output = toolbox.execute(item.name, item.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": item.id,
                            "content": output,
                        }
                    )
            except TaskFinished as done:
                final_summary = done.summary.strip() or toolbox.finished_summary or turn.text.strip()
                break
            except Exception as exc:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": item.id,
                        "content": f"Error: {exc}",
                        "is_error": True,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        if not final_summary:
            raise RuntimeError(f"LLM did not finish within {step_limit} steps")
        return final_summary

    def _execute_with_text_actions(
        self,
        *,
        runtime: Any,
        run: dict[str, Any],
        task: dict[str, Any],
        agent_id: str,
        toolbox: WorkspaceToolbox,
        system_prompt: str,
        messages: list[dict[str, Any]],
        trace_lines: list[str],
        max_steps: int,
    ) -> str:
        step_limit = max(6, min(int(max_steps or DEFAULT_MAX_STEPS), 50))
        for step in range(1, step_limit + 1):
            runtime.events.emit(
                "llm.turn.started",
                run_id=run["id"],
                task_id=task["id"],
                agent_id=agent_id,
                step=step,
                model=self.config.get("model", ""),
            )
            turn = self.client.complete(
                system=system_prompt,
                messages=messages,
                tools=[],
                max_tokens=2400,
            )
            assistant_text = turn.text.strip()
            action = self._parse_text_action(assistant_text)
            messages.append({"role": "assistant", "content": self._conversation_assistant_content(assistant_text, action)})
            if assistant_text:
                runtime.events.emit(
                    "llm.turn.text",
                    run_id=run["id"],
                    task_id=task["id"],
                    agent_id=agent_id,
                    step=step,
                    detail=assistant_text[:2000],
                )
            trace_lines.append(
                json.dumps(
                    {
                        "ts": now_ts(),
                        "mode": "text_actions",
                        "step": step,
                        "assistant_text": assistant_text,
                        "parsed_action": action,
                    },
                    ensure_ascii=True,
                )
            )
            runtime.events.emit(
                "llm.turn.finished",
                run_id=run["id"],
                task_id=task["id"],
                agent_id=agent_id,
                step=step,
                stop_reason=turn.stop_reason,
                tool_use_count=1 if action else 0,
            )
            if not action:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply could not be parsed. Reply with exactly one action only. "
                            "Preferred format:\nACTION: read_file\nPATH: app.py\n\n"
                            "Or for commands:\nACTION: run_command\nCOMMAND <<'CMD'\npython3 -m py_compile app.py\nCMD\n\n"
                            "You may also send a single JSON action object as fallback."
                        ),
                    }
                )
                continue
            tool_name = str(action.get("tool", "")).strip()
            payload = action.get("input", {})
            if not isinstance(payload, dict):
                payload = {}
            try:
                if tool_name == "finish_task":
                    payload = {"summary": str(payload.get("summary", "")).strip() or assistant_text}
                tool_output = toolbox.execute(tool_name, payload)
                messages.append(
                    {
                        "role": "user",
                        "content": self._conversation_tool_feedback(tool_name, tool_output),
                    }
                )
            except TaskFinished as done:
                return done.summary.strip() or toolbox.finished_summary or assistant_text
            except Exception as exc:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool execution error for {tool_name}: {exc}\n"
                            "Adjust your approach and reply with exactly one next action. Prefer the ACTION:/COMMAND block format."
                        ),
                    }
                )
        raise RuntimeError(f"LLM did not finish within {step_limit} steps")

    def _conversation_assistant_content(self, assistant_text: str, action: dict[str, Any] | None) -> str:
        if not action:
            compact = assistant_text.strip()
            if len(compact) <= 1200:
                return compact
            return compact[:1200] + "\n...[truncated by RepoPilot]"
        tool_name = str(action.get("tool", "")).strip()
        payload = action.get("input", {})
        if not isinstance(payload, dict):
            payload = {}
        lines = [f"ACTION: {tool_name}"]
        ordered_keys = (
            "path",
            "query",
            "glob",
            "limit",
            "start_line",
            "end_line",
            "timeout_seconds",
            "replace_all",
            "command",
            "content",
            "old_text",
            "new_text",
            "summary",
        )
        for key in ordered_keys:
            if key not in payload:
                continue
            value = payload[key]
            label = key.upper()
            if isinstance(value, str):
                text = value.strip()
                if "\n" in text or len(text) > 180:
                    lines.append(f"{label}: [{len(text)} chars omitted from history]")
                else:
                    lines.append(f"{label}: {text}")
                continue
            lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def _conversation_tool_feedback(self, tool_name: str, tool_output: str) -> str:
        safe_output = tool_output.strip()
        if len(safe_output) > 4000:
            safe_output = safe_output[:4000] + "\n...[truncated by RepoPilot]"
        return (
            f"Tool result for {tool_name}:\n{safe_output}\n\n"
            "Reply with exactly one next action. Prefer the ACTION:/COMMAND block format."
        )

    def _parse_text_action(self, text: str) -> dict[str, Any] | None:
        block_action = self._parse_block_action(text)
        if block_action:
            return block_action
        for candidate in self._extract_json_objects(text):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            tool_name = str(parsed.get("tool", "")).strip()
            if not tool_name:
                continue
            parsed["tool"] = self._normalize_tool_name(tool_name)
            payload = parsed.get("input", {})
            if not isinstance(payload, dict):
                parsed["input"] = {}
            return parsed
        return None

    def _parse_block_action(self, text: str) -> dict[str, Any] | None:
        lines = text.strip().splitlines()
        if not lines:
            return None
        first = lines[0].strip()
        prefix = "ACTION:"
        if first.upper().startswith("TOOL:"):
            prefix = "TOOL:"
        if not first.upper().startswith(prefix):
            return None

        tool_name = self._normalize_tool_name(first.split(":", 1)[1].strip())
        payload: dict[str, Any] = {}
        idx = 1
        while idx < len(lines):
            line = lines[idx].rstrip()
            stripped = line.strip()
            idx += 1
            if not stripped:
                continue
            upper = stripped.upper()
            if upper.startswith("PATH:"):
                payload["path"] = stripped.split(":", 1)[1].strip()
                continue
            if upper.startswith("QUERY:"):
                payload["query"] = stripped.split(":", 1)[1].strip()
                continue
            if upper.startswith("GLOB:"):
                payload["glob"] = stripped.split(":", 1)[1].strip()
                continue
            if upper.startswith("LIMIT:"):
                raw = stripped.split(":", 1)[1].strip()
                if raw.isdigit():
                    payload["limit"] = int(raw)
                continue
            if upper.startswith("START_LINE:"):
                raw = stripped.split(":", 1)[1].strip()
                if raw.isdigit():
                    payload["start_line"] = int(raw)
                continue
            if upper.startswith("END_LINE:"):
                raw = stripped.split(":", 1)[1].strip()
                if raw.isdigit():
                    payload["end_line"] = int(raw)
                continue
            if upper.startswith("TIMEOUT_SECONDS:"):
                raw = stripped.split(":", 1)[1].strip()
                if raw.isdigit():
                    payload["timeout_seconds"] = int(raw)
                continue
            if upper.startswith("REPLACE_ALL:"):
                raw = stripped.split(":", 1)[1].strip().lower()
                payload["replace_all"] = raw in {"1", "true", "yes"}
                continue
            block_value, next_idx = self._parse_block_value(lines, idx - 1, "COMMAND")
            if block_value is not None:
                payload["command"] = block_value
                idx = next_idx
                continue
            block_value, next_idx = self._parse_block_value(lines, idx - 1, "CONTENT")
            if block_value is not None:
                payload["content"] = block_value
                idx = next_idx
                continue
            tail_value, next_idx = self._parse_multiline_tail(lines, idx - 1, "CONTENT")
            if tail_value is not None:
                payload["content"] = tail_value
                idx = next_idx
                continue
            block_value, next_idx = self._parse_block_value(lines, idx - 1, "OLD_TEXT")
            if block_value is not None:
                payload["old_text"] = block_value
                idx = next_idx
                continue
            tail_value, next_idx = self._parse_multiline_tail(lines, idx - 1, "OLD_TEXT")
            if tail_value is not None:
                payload["old_text"] = tail_value
                idx = next_idx
                continue
            block_value, next_idx = self._parse_block_value(lines, idx - 1, "NEW_TEXT")
            if block_value is not None:
                payload["new_text"] = block_value
                idx = next_idx
                continue
            tail_value, next_idx = self._parse_multiline_tail(lines, idx - 1, "NEW_TEXT")
            if tail_value is not None:
                payload["new_text"] = tail_value
                idx = next_idx
                continue
            block_value, next_idx = self._parse_block_value(lines, idx - 1, "SUMMARY")
            if block_value is not None:
                payload["summary"] = block_value
                idx = next_idx
                continue
            tail_value, next_idx = self._parse_multiline_tail(lines, idx - 1, "SUMMARY")
            if tail_value is not None:
                payload["summary"] = tail_value
                idx = next_idx
                continue
            if upper.startswith("COMMAND:"):
                payload["command"] = stripped.split(":", 1)[1].strip()
                continue
            if upper.startswith("SUMMARY:"):
                payload["summary"] = stripped.split(":", 1)[1].strip()
                continue
        if not tool_name:
            return None
        return {"tool": tool_name, "input": payload}

    def _parse_block_value(self, lines: list[str], start_idx: int, label: str) -> tuple[str | None, int]:
        line = lines[start_idx].strip()
        prefix = f"{label} <<"
        if not line.upper().startswith(prefix):
            return None, start_idx + 1
        marker = line.split("<<", 1)[1].strip()
        marker = marker.strip("'\"")
        block_lines: list[str] = []
        idx = start_idx + 1
        while idx < len(lines):
            current = lines[idx]
            if current.strip() == marker:
                return "\n".join(block_lines), idx + 1
            block_lines.append(current)
            idx += 1
        return "\n".join(block_lines), idx

    def _parse_multiline_tail(self, lines: list[str], start_idx: int, label: str) -> tuple[str | None, int]:
        line = lines[start_idx]
        stripped = line.strip()
        if not stripped.upper().startswith(f"{label}:"):
            return None, start_idx + 1
        inline = stripped.split(":", 1)[1].lstrip()
        parts: list[str] = [inline] if inline else []
        idx = start_idx + 1
        while idx < len(lines):
            parts.append(lines[idx])
            idx += 1
        return "\n".join(parts).rstrip(), idx

    def _extract_json_objects(self, text: str) -> list[str]:
        candidate = text.strip()
        if not candidate:
            return []
        objects: list[str] = []
        start = -1
        depth = 0
        in_string = False
        escape = False
        for idx, char in enumerate(candidate):
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                if depth == 0:
                    start = idx
                depth += 1
            elif char == "}":
                if depth == 0:
                    continue
                depth -= 1
                if depth == 0 and start != -1:
                    objects.append(candidate[start : idx + 1])
                    start = -1
        return objects

    def _normalize_tool_name(self, name: str) -> str:
        aliases = {
            "list_directory": "list_files",
            "list_dir": "list_files",
            "ls": "list_files",
            "search": "search_files",
            "grep": "search_files",
            "read": "read_file",
            "open_file": "read_file",
            "write": "write_file",
            "create_file": "write_file",
            "replace_in_file": "edit_file",
            "shell": "run_command",
            "bash": "run_command",
            "run_shell": "run_command",
            "complete_task": "finish_task",
            "done": "finish_task",
        }
        normalized = name.strip()
        return aliases.get(normalized, normalized)

    def _build_system_prompt(
        self,
        *,
        task: dict[str, Any],
        run: dict[str, Any],
        worktree: dict[str, Any],
        native_tools: bool,
    ) -> str:
        base = (
            "You are RepoPilot, a real coding agent running inside an isolated workspace. "
            "Inspect the codebase with tools before editing, make concrete code changes that satisfy the assigned task, "
            "and verify your work with commands when useful. Stay inside the provided workspace only. "
            "Prefer minimal, correct edits over broad rewrites. When the task is complete, call finish_task with a concise delivery summary. "
            "Important: on some compatible endpoints, large write_file payloads may be truncated. "
            "For larger file rewrites, prefer run_command with python3 - <<'PY' and Path(...).write_text(...) or use several smaller edit_file operations. "
            f"Mission: {run['mission']} | Task: {task['title']} | Workspace: {worktree['path']}"
        )
        if native_tools:
            return base
        return (
            base
            + " Do not rely on API-native tool use. Prefer the block action protocol below and reply with exactly one action and nothing else.\n"
            + "Example read:\nACTION: read_file\nPATH: app.py\n\n"
            + "Example command:\nACTION: run_command\nCOMMAND <<'CMD'\npython3 - <<'PY'\nfrom pathlib import Path\nPath('app.py').write_text('print(\\\"hi\\\")\\n', encoding='utf-8')\nPY\nCMD\n\n"
            + "Example finish:\nACTION: finish_task\nSUMMARY: what changed and how it was verified\n\n"
            + "JSON actions are still accepted as a fallback, but the block protocol is preferred for multi-line commands and file content."
        )

    def _build_user_prompt(
        self,
        *,
        run: dict[str, Any],
        task: dict[str, Any],
        worktree: dict[str, Any],
        prompt_path: Path,
        task_brief_path: Path,
        notes_path: Path,
        repo_profile: dict[str, Any],
    ) -> str:
        criteria = "\n".join(f"- {item}" for item in task.get("acceptance_criteria", [])) or "- (none)"
        validations = "\n".join(f"- {item}" for item in run.get("validation_commands", [])) or "- (none)"
        focus = task.get("focus", "general")
        focus_guidance = {
            "frontend": (
                "Primary target files are frontend assets such as static HTML/CSS/JS. "
                "Avoid backend-only files unless a small integration touch is necessary."
            ),
            "backend": (
                "Primary target files are backend/server/data files such as app.py. "
                "Avoid broad frontend rewrites unless required for integration."
            ),
        }.get(focus, "Keep edits tightly scoped to this task's responsibility.")
        return (
            f"Mission: {run['mission']}\n\n"
            f"Task title: {task['title']}\n"
            f"Task description: {task['description']}\n"
            f"Focus: {focus}\n"
            f"Focus guidance: {focus_guidance}\n\n"
            f"Operator constraints:\n{run.get('operator_constraints') or '(none)'}\n\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            f"Workspace root: {worktree['path']}\n"
            f"Task brief file: {task_brief_path.name}\n"
            f"Prompt file: {prompt_path.name}\n"
            f"Notes file: {notes_path.relative_to(Path(worktree['path']))}\n\n"
            f"Validation commands configured for the overall run:\n{validations}\n\n"
            "Repo profile snapshot:\n"
            f"- top level: {', '.join(repo_profile.get('top_level', [])[:20])}\n"
            f"- sample files: {', '.join(repo_profile.get('sample_files', [])[:20])}\n\n"
            "Start by inspecting the relevant files. Do not claim success before you have made actual file changes for this task. "
            "If you need to create or replace a large file, prefer run_command with python3 - <<'PY' and Path.write_text(...) over a giant write_file payload."
        )
