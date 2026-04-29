# RepoPilot Harness Deployment Runbook

This document explains how to run, configure, operate, and troubleshoot `RepoPilot Harness` as a real local-first harness application.

## 1. What this service is

RepoPilot Harness is a FastAPI application that serves an operator console and manages execution runs against a target repository.

A run typically goes through these phases:

1. operator submits a mission
2. planner generates a task graph
3. runtime materializes durable tasks
4. operator approves execution
5. runtime creates isolated workspaces
6. implementation step executes in each workspace
7. validation commands execute against produced outputs
8. artifacts, events, and summaries are persisted for review

## 2. Prerequisites

Required:

- Python `3.11+`
- `pip`
- a repository you want RepoPilot to operate on

Optional but recommended:

- `git` for true worktree-based isolation
- a configured LLM endpoint for `direct_llm` mode
- a coding agent CLI such as `codex` for `agent_command` mode

## 3. Install and bootstrap

From the repository root:

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Minimum `.env` for `direct_llm` mode:

```dotenv
ANTHROPIC_API_KEY=your_api_key
MODEL_ID=claude-opus-4-1
# Optional for compatible providers or proxy endpoints
ANTHROPIC_BASE_URL=
```

Optional runtime knobs:

```dotenv
REPOPILOT_LLM_TIMEOUT_SECONDS=90
REPOPILOT_LLM_MAX_STEPS=18
REPOPILOT_AGENT_TIMEOUT_SECONDS=900
REPOPILOT_AGENT_COMMAND=codex exec --full-auto --dangerously-bypass-approvals-and-sandbox "$(cat \"$REPOPILOT_PROMPT_FILE\")"
```

## 4. Start the service

Recommended command from the repo root:

```bash
python3 products/repopilot_harness/backend/main.py
```

Default bind address:

- host: `127.0.0.1`
- port: `8877`

Open in browser:

```text
http://127.0.0.1:8877
```

Alternative Uvicorn invocation:

```bash
cd products/repopilot_harness/backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8877
```

## 5. Choosing an execution mode

### Mode A: `scaffold`

Use this for harness dry-runs.

Behavior:

- creates the run
- plans tasks
- creates workspaces and briefs
- prepares validation commands
- does not invoke implementation execution

Best for:

- debugging task planning
- testing workspace creation
- demonstrating the control plane without spending tokens

### Mode B: `direct_llm`

Use this when you want RepoPilot itself to be the runtime.

Requirements:

- `ANTHROPIC_API_KEY`
- `MODEL_ID`
- optional `ANTHROPIC_BASE_URL` for proxy-compatible endpoints

Behavior:

- RepoPilot calls the LLM API directly
- the runtime performs implementation steps inside the isolated workspace
- validation still runs locally after generation

Best for:

- learning how to build an in-house agent runtime
- avoiding dependence on an external coding agent CLI

### Mode C: `agent_command`

Use this when RepoPilot should orchestrate a separate coding agent.

Requirements:

- an installed CLI agent, or a custom command via `REPOPILOT_AGENT_COMMAND`

Behavior:

- RepoPilot writes execution prompts and task metadata into the workspace
- RepoPilot invokes the command inside that workspace
- the external runtime modifies files
- RepoPilot then runs validation and persists artifacts

Best for:

- plugging RepoPilot into an existing agent stack
- comparing runtime orchestration with different agent backends

## 6. Environment variables passed to agent-command execution

When RepoPilot runs `agent_command`, it injects:

- `REPOPILOT_PROMPT_FILE`
- `REPOPILOT_TASK_FILE`
- `REPOPILOT_WORKTREE`
- `REPOPILOT_TASK_ID`
- `REPOPILOT_TASK_TITLE`
- `REPOPILOT_MISSION`
- `REPOPILOT_REPO_ROOT`

This lets the external runtime read a prepared prompt and understand the exact task context.

## 7. Operator workflow

A typical operator session looks like this:

1. open the console
2. confirm the target repository root
3. enter a mission
4. optionally add constraints and validation commands
5. choose `scaffold`, `direct_llm`, or `agent_command`
6. create the run
7. inspect the planned task graph
8. approve execution
9. watch tasks, jobs, and events update in real time
10. inspect artifacts and workspace files before deciding next actions

## 8. Runtime storage layout

For a target repository `repo_root`, RepoPilot writes:

```text
repo_root/
├── .repopilot_harness/
│   ├── events.jsonl
│   ├── jobs/
│   ├── runs/
│   ├── tasks/
│   ├── worktrees/
│   ├── artifacts/
│   ├── summaries/
│   └── runtime_session.json
└── .repopilot_harness_worktrees/
```

Interpretation:

- `runs/`: top-level run metadata
- `tasks/`: durable task state
- `jobs/`: implementation and validation job records
- `events.jsonl`: append-only event timeline
- `artifacts/`: generated delivery files
- `summaries/`: per-run summaries
- `.repopilot_harness_worktrees/`: implementation workspaces

These paths are runtime state, not source code.

## 9. Safety and operational notes

Current implementation includes basic command safety checks, but this is still a local engineering tool, not a hardened multi-tenant production platform.

Practical guidance:

- run against repositories you control
- review custom validation commands carefully
- prefer `scaffold` mode when testing a new environment
- keep `.repopilot_harness/` and `.repopilot_harness_worktrees/` out of git
- use a dedicated test repository when validating new execution commands

## 10. Troubleshooting

### Problem: the UI opens but real execution is unavailable

Check `GET /api/meta` behavior in the UI defaults.

Likely causes:

- no `ANTHROPIC_API_KEY`
- no `MODEL_ID`
- no `REPOPILOT_AGENT_COMMAND`
- no `codex` binary in `PATH`

### Problem: `direct_llm` cannot start

Confirm:

- `.env` exists in the repo root
- `ANTHROPIC_API_KEY` is non-empty
- `MODEL_ID` is non-empty
- if using a compatible provider, `ANTHROPIC_BASE_URL` is correct

### Problem: target repository has no commits

This is supported.

RepoPilot falls back from Git worktree creation to an isolated Git-initialized workspace copy.

### Problem: validation fails even though files were generated

Check:

- the validation commands are correct for the target repo
- generated files landed in the implementation workspace you expect
- the task output actually satisfies the validation command assumptions

### Problem: process restarts mid-run

RepoPilot marks in-flight runs as paused after restart so the operator can inspect and re-approve.

## 11. API surface

The current backend exposes these primary endpoints:

- `GET /`
- `GET /api/meta`
- `GET /api/runs`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/approve`
- `POST /api/runs/{run_id}/pause`
- `POST /api/tasks/{task_id}/retry`
- `GET /api/worktrees/{worktree_id}/files`
- `GET /api/file?path=...`
- `WS /ws/events`

This is enough for a browser console, lightweight operator tools, or future alternate frontends.

## 12. Suggested next hardening steps

If you want to evolve RepoPilot toward a stronger production-grade internal tool, do these next:

1. add command allowlists / policy enforcement
2. add workspace diff and merge workflows
3. add authentication and multi-user operator audit trails
4. replace JSON-file persistence with a transactional store
5. add explicit budget / token / timeout governance per run
6. add richer protocol-driven specialist agents
