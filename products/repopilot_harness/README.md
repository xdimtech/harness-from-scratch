# RepoPilot Harness

RepoPilot Harness is a local-first engineering control plane that turns a natural-language software request into a managed delivery run:

`mission intake -> planning -> task graph -> isolated workspaces -> implementation execution -> validation -> delivery handoff`

## What is working now

- FastAPI backend with durable run, task, job, workspace, and event persistence.
- Operator console UI for creating runs, approving execution, inspecting task state, job logs, workspaces, artifacts, and files.
- Real isolated execution workspaces per implementation task.
- Recovery support: in-flight runs are reopened in a paused state after process restart.
- Two execution modes:
  - `scaffold`: prepare workspaces and handoff artifacts only.
  - `agent_command`: run a real implementation command inside each workspace.
- Validation jobs run against produced workspaces and stream output into persisted job logs.

## Start the console

```bash
python3 products/repopilot_harness/backend/main.py
```

Open:

```text
http://127.0.0.1:8877
```

## Execution modes

### 1. Scaffold mode

Use this when you want the harness to plan the run, create isolated workspaces, write task briefs, and run validations without invoking an external coding agent.

### 2. Agent command mode

Use this when you want RepoPilot to execute a real implementation command in each workspace.

The command runs with these environment variables:

- `REPOPILOT_PROMPT_FILE`
- `REPOPILOT_TASK_FILE`
- `REPOPILOT_WORKTREE`
- `REPOPILOT_TASK_ID`
- `REPOPILOT_TASK_TITLE`
- `REPOPILOT_MISSION`
- `REPOPILOT_REPO_ROOT`

Example command:

```bash
codex exec --full-auto "$(cat \"$REPOPILOT_PROMPT_FILE\")"
```

You can also preconfigure defaults:

```bash
export REPOPILOT_AGENT_COMMAND='codex exec --full-auto "$(cat \"$REPOPILOT_PROMPT_FILE\")"'
export REPOPILOT_AGENT_TIMEOUT_SECONDS=1800
```

When `REPOPILOT_AGENT_COMMAND` is set, the UI defaults to `agent_command` mode.

## Runtime data

Per target repository, RepoPilot stores runtime state under:

- `.repopilot_harness/`
- `.repopilot_harness_worktrees/`

Important subdirectories:

- `.repopilot_harness/runs/`
- `.repopilot_harness/tasks/`
- `.repopilot_harness/jobs/`
- `.repopilot_harness/worktrees/`
- `.repopilot_harness/artifacts/`
- `.repopilot_harness/summaries/`

## Current product behavior

- If the repository has commits, RepoPilot creates a Git worktree.
- If the repository has no `HEAD` yet, RepoPilot falls back to an isolated Git-initialized workspace copy.
- RepoPilot copies the current working tree snapshot into each workspace, so uncommitted and untracked files are available for execution and validation.
- Validation commands are executed in each produced implementation workspace.

## Tests

```bash
python3 -m pytest products/repopilot_harness/tests/test_repopilot_harness_app.py -q
```

## Next product upgrades

- Add merge / discard controls for produced workspaces.
- Add richer policy controls for allowed commands.
- Add deeper protocol-driven multi-agent execution beyond a single command template.
- Add diff-aware review and patch approval flows.
