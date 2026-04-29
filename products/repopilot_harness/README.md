# RepoPilot Harness

RepoPilot Harness is a local-first engineering control plane for turning a natural-language software mission into a structured delivery run.

Core flow:

`mission intake -> planning -> task graph -> isolated workspaces -> implementation execution -> validation -> delivery handoff`

## Demo snapshot

Current console snapshot:

![RepoPilot Harness demo console](../../docs/assets/demo.png)

## What the product does today

- creates durable runs, tasks, jobs, workspaces, artifacts, and event history
- provides an operator console UI for mission submission and run supervision
- supports approval, pause, retry, and restart recovery flows
- creates isolated per-task workspaces for implementation execution
- supports both direct LLM execution and external agent-command execution
- persists validation logs and produced artifacts for inspection
- lets the operator inspect worktree files directly in the UI

## Product layout

```text
products/repopilot_harness/
├── backend/
│   ├── main.py                     # FastAPI + Uvicorn entrypoint
│   └── app/
│       ├── api.py                  # HTTP / WebSocket API and UI serving
│       ├── runtime.py              # Core harness runtime
│       ├── planner.py              # Mission to task-graph planning
│       ├── store.py                # Durable JSON-backed stores
│       ├── llm_client.py           # Direct LLM runtime integration
│       ├── direct_llm_executor.py  # Direct agent execution loop
│       └── ui_static/              # Console UI assets
├── docs/
│   └── deployment-runbook.md       # Setup, run, deployment, operations
└── tests/
    └── test_repopilot_harness_app.py
```

## Execution modes

### `scaffold`

RepoPilot plans the run, creates workspaces, writes task briefs and prompts, and prepares validation jobs, but does not invoke a coding runtime.

Use this when you want a safe dry-run of the harness.

### `direct_llm`

RepoPilot itself calls the configured LLM API and performs the implementation loop inside each isolated workspace.

Use this when you want to build your own runtime layer from scratch instead of shelling out to another coding agent.

### `agent_command`

RepoPilot invokes a real external agent command inside each workspace.

Typical example:

```bash
codex exec --full-auto --dangerously-bypass-approvals-and-sandbox "$(cat \"$REPOPILOT_PROMPT_FILE\")"
```

Use this when you want RepoPilot to orchestrate an external coding agent rather than implement the full execution layer internally.

## Quick start

From the repository root:

```bash
cp .env.example .env
python3 -m pip install -r requirements.txt
python3 products/repopilot_harness/backend/main.py
```

Then open:

```text
http://127.0.0.1:8877
```

## Recommended first demo

Use the current repository itself as the target repo root, then submit a mission such as:

```text
Build a small local Python CLI tool with input validation and a short README.
```

That gives you a realistic first run while keeping the validation loop simple.

## Runtime state

For each target repository, RepoPilot writes runtime data under:

- `.repopilot_harness/`
- `.repopilot_harness_worktrees/`

Important subdirectories include:

- `.repopilot_harness/runs/`
- `.repopilot_harness/tasks/`
- `.repopilot_harness/jobs/`
- `.repopilot_harness/worktrees/`
- `.repopilot_harness/artifacts/`
- `.repopilot_harness/summaries/`

These directories should not be committed into the target repository.

## Operational behavior

- if the target repository has commits, RepoPilot creates Git worktrees
- if the target repository has no `HEAD`, RepoPilot falls back to an isolated Git-initialized workspace copy
- the runtime copies the current working tree snapshot into each workspace, so uncommitted and untracked files are available to implementation and validation steps
- validation commands run inside the produced implementation workspace
- in-flight runs are re-opened into a paused state after a process restart so the operator can decide whether to resume

## Tests

```bash
python3 -m pytest products/repopilot_harness/tests/test_repopilot_harness_app.py -q
```

## Documentation

- product runbook: `products/repopilot_harness/docs/deployment-runbook.md`
- graduation-project overview: `docs/course-graduation-review-zh.md`
- console demo note: `docs/repopilot-console-demo-zh.md`

## Near-term roadmap

- merge / discard controls for produced workspaces
- diff-aware review and patch approval flows
- richer policy controls for allowed commands
- stronger protocol-driven multi-agent execution beyond a single command template
- better replay and operator observability
