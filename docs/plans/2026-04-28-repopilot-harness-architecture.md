# RepoPilot Harness Product Architecture Plan

## 1. Product Definition

### 1.1 One-line Goal

RepoPilot Harness is a local-first AI engineering runtime that converts a natural-language software request into a controlled delivery pipeline:

`request -> planning -> task graph -> multi-agent execution -> isolated worktrees -> validation -> operator review -> delivery`

### 1.2 What We Are Building

This product is not a chatbot and not a toy observability demo. It is a production-grade local application with a real backend runtime and a real frontend console for operating software delivery work.

The product must let an operator:

- submit a software task in natural language
- see how the system decomposes the task into executable units
- watch multiple agents coordinate through explicit protocols
- execute tasks in isolated Git worktrees
- run real commands such as tests, lint, type checks, and builds
- inspect logs, diffs, files, and validation outcomes
- decide whether to keep, merge, retry, or discard results

### 1.3 Product Positioning

RepoPilot Harness is a local AI engineering coordination platform.

It sits between:

- a developer prompt box, which is too unstructured for reliable delivery
- a CI pipeline, which validates changes but does not organize agent work

Its core value is controlled autonomy: agents can execute real work, but only inside a harness with explicit task state, workspace isolation, validation gates, and operator controls.

## 2. Users, Use Cases, and Scope

### 2.1 Primary User

The primary user is a technical operator:

- solo developer
- tech lead
- AI engineer
- internal tooling builder

This user wants real output, not a conversation transcript.

### 2.2 Core Use Cases

1. Create a feature request and let the harness break it into tasks.
2. Run multiple implementation lanes in parallel without file conflicts.
3. Validate generated work with tests and build commands.
4. Review code artifacts and logs before accepting results.
5. Replay the full execution trail after the run completes.
6. Resume, retry, or repair failed tasks without losing context.

### 2.3 Out of Scope for V1

To keep the product coherent, V1 does not include:

- cloud multi-tenant deployment
- auto-merge directly into protected branches
- IDE plugin support
- enterprise RBAC and SSO
- distributed worker clusters
- model vendor marketplace

These can be added after the local product is stable.

## 3. Product Principles

The architecture and UX should follow these principles:

1. Local-first by default.
2. Real execution over simulated events.
3. Strong operator control over autonomous behavior.
4. Isolate file mutations by task.
5. Persist everything important.
6. Prefer restartable state machines over fragile long-lived memory.
7. Separate product code from course/demo code.
8. Make every step inspectable, replayable, and debuggable.

## 4. Functional Requirements

### 4.1 Intake and Planning

The system must:

- accept a mission in natural language
- capture repository context and operator constraints
- generate a structured plan
- turn that plan into a task graph
- allow operator review before execution begins

### 4.2 Task System

The system must:

- persist tasks with lifecycle state
- express dependencies between tasks
- support claim, release, retry, cancel, block, and complete actions
- record assignment, timestamps, and artifacts
- support both human-created and agent-created follow-up tasks

### 4.3 Multi-Agent Execution

The system must:

- support multiple agent roles
- allow agent-to-agent messaging through defined protocols
- support planner, implementer, reviewer, validator, and doc roles
- keep agent memory bounded and compactable
- support resuming interrupted runs

### 4.4 Worktree Isolation

The system must:

- create a unique Git worktree for each executable task
- bind the worktree to a task and owning agent
- collect diff and artifact metadata from that workspace
- allow keep, prune, retry, and inspect actions

### 4.5 Validation and Jobs

The system must:

- run test, lint, type-check, build, and custom commands as background jobs
- stream job state and output
- associate jobs with task and run ids
- surface pass/fail status to the operator and task state machine

### 4.6 Review and Delivery

The system must:

- show task timeline, logs, diffs, files, and validation results
- allow operator approval or rejection
- generate a run summary with deliverables and unresolved issues
- retain artifacts for audit and debugging

## 5. Non-Functional Requirements

### 5.1 Reliability

- crash-safe state persistence
- idempotent recovery after restart
- durable event history
- bounded failure domains per task

### 5.2 Performance

- UI should feel responsive under hundreds of events per run
- background execution must not block the API thread
- file tree and log retrieval must support pagination and truncation

### 5.3 Maintainability

- explicit module boundaries
- testable domain services
- narrow interfaces between runtime components
- all major architecture decisions documented as ADRs

### 5.4 Security

- dangerous shell commands filtered or policy-gated
- workspace and file access scoped to the project root and spawned worktrees
- secret values redacted from logs where possible
- explicit command provenance attached to each execution

### 5.5 Observability

- structured event stream
- persisted run timeline
- command, agent, and task level status
- failure diagnostics available after the fact

## 6. Architecture Overview

### 6.1 High-Level Architecture

```text
+-------------------------------------------------------------------+
|                        RepoPilot Harness UI                        |
|-------------------------------------------------------------------|
| Mission Intake | Run Board | Task Graph | Logs | Diff | Artifacts |
+-----------------------------|-------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                           FastAPI Backend                          |
|-------------------------------------------------------------------|
| API Layer | WebSocket/Event Stream | Auth/Policy | View Models    |
+-----------------------------|-------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                         Application Services                       |
|-------------------------------------------------------------------|
| Run Service | Planning Service | Task Service | Review Service    |
| Agent Orchestrator | Job Service | Worktree Service | Summary      |
+-----------------------------|-------------------------------------+
                              |
          +-------------------+-------------------+----------------+
          |                   |                   |                |
          v                   v                   v                v
+----------------+   +----------------+   +----------------+  +----------------+
| Agent Runtime  |   | Job Runner     |   | Protocol Bus   |  | Artifact Index |
| loops/roles    |   | test/lint/build|   | inbox/outbox   |  | files/diffs/logs|
+----------------+   +----------------+   +----------------+  +----------------+
          |                   |                   |                |
          +-------------------+-------------------+----------------+
                              |
                              v
+-------------------------------------------------------------------+
|                           Infrastructure                           |
|-------------------------------------------------------------------|
| Git Worktrees | Local Files | JSONL/Event Store | Task Store      |
| Run Store     | Job Store   | Summary Store     | Transcript Store |
+-------------------------------------------------------------------+
```

### 6.2 Architectural Style

Recommended style for V1: modular monolith.

Reason:

- the product is local-first
- operational complexity should stay low
- backend concerns are tightly coupled around a single runtime
- strong internal boundaries are more useful than premature services

We will build clear modules that can later be split, but we will not start with microservices.

## 7. Repository Strategy

### 7.1 Separation from Course Material

The existing repository remains the learning workspace. Product code should live in a dedicated root to avoid mixing educational scripts with runtime code.

Recommended product location:

```text
products/repopilot_harness/
  backend/
  frontend/
  shared/
  tests/
  docs/
```

### 7.2 Internal Layout

```text
products/repopilot_harness/
  backend/
    app/
      api/
      domain/
      services/
      runtime/
      protocols/
      persistence/
      security/
      schemas/
      ui_static/
    main.py
  frontend/
    design/
    specs/
  tests/
    unit/
    integration/
    e2e/
  docs/
    adr/
    diagrams/
```

Notes:

- `backend/app/domain` holds business entities and state rules.
- `backend/app/services` coordinates use cases.
- `backend/app/runtime` holds long-running execution loops.
- `backend/app/persistence` isolates file-based stores from domain logic.
- `backend/app/ui_static` serves the initial production console without introducing a JS build chain too early.

## 8. Domain Model

### 8.1 Core Entities

#### Run

Represents one operator-submitted mission.

Fields:

- `id`
- `slug`
- `mission`
- `repo_root`
- `status`
- `created_at`
- `updated_at`
- `plan`
- `summary`
- `operator_constraints`
- `task_ids`

Statuses:

- `draft`
- `planned`
- `approved`
- `running`
- `paused`
- `failed`
- `completed`
- `archived`

#### Task

Represents one executable or reviewable unit.

Fields:

- `id`
- `run_id`
- `title`
- `description`
- `kind`
- `role_required`
- `status`
- `depends_on`
- `priority`
- `owner_agent_id`
- `worktree_id`
- `artifact_refs`
- `job_ids`
- `acceptance_criteria`
- `created_at`
- `updated_at`

Statuses:

- `todo`
- `ready`
- `claimed`
- `executing`
- `validating`
- `blocked`
- `review_required`
- `completed`
- `failed`
- `canceled`

#### Agent

Represents a runtime worker with a role and bounded memory.

Fields:

- `id`
- `role`
- `state`
- `current_task_id`
- `worktree_id`
- `capabilities`
- `memory_ref`
- `protocol_version`

#### Worktree

Represents an isolated Git workspace.

Fields:

- `id`
- `run_id`
- `task_id`
- `path`
- `branch_name`
- `status`
- `owner_agent_id`
- `diff_summary`

#### Job

Represents a background command execution.

Fields:

- `id`
- `run_id`
- `task_id`
- `kind`
- `command`
- `cwd`
- `status`
- `exit_code`
- `stdout_path`
- `stderr_path`
- `started_at`
- `finished_at`

#### Event

Represents a durable timeline record.

Fields:

- `id`
- `run_id`
- `task_id`
- `agent_id`
- `type`
- `payload`
- `ts`

## 9. Backend Design

### 9.1 API Layer

The backend should expose two classes of interfaces:

1. command APIs for mutations
2. query APIs for UI reads

Recommended initial endpoints:

- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/plan`
- `POST /api/runs/{run_id}/approve`
- `POST /api/runs/{run_id}/pause`
- `POST /api/runs/{run_id}/resume`
- `GET /api/runs/{run_id}/tasks`
- `POST /api/tasks/{task_id}/retry`
- `POST /api/tasks/{task_id}/cancel`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/worktrees/{worktree_id}/tree`
- `GET /api/files`
- `GET /api/jobs/{job_id}`
- `GET /api/events`
- `WS /ws/events`

### 9.2 Application Services

#### Run Service

Responsible for run creation, plan approval, start, pause, resume, and final summary.

#### Planning Service

Responsible for converting natural language request into a structured implementation plan and task graph.

In V1, the planning flow should support two modes:

- deterministic scaffold planner for stable testability
- model-assisted planner for richer decomposition when configured

#### Task Service

Owns task state transitions and dependency resolution.

#### Agent Orchestrator

Boots role workers, dispatches available tasks, handles retries, and coordinates agent lifecycle.

#### Worktree Service

Creates, tracks, repairs, and removes Git worktrees.

#### Job Service

Runs and monitors background commands with streamed output and durable logs.

#### Review Service

Collects diffs, artifact references, validation status, and review summaries.

### 9.3 Runtime Loops

The runtime should not rely on one monolithic thread. Instead, use several cooperating loops:

- dispatcher loop: finds ready tasks and assigns them
- agent loop: each agent claims and executes work
- validation loop: schedules tests after implementation tasks finish
- cleanup loop: handles retention and pruning rules
- websocket broadcast loop: publishes new events to clients

This structure keeps each concern simpler and improves restartability.

## 10. Agent System Design

### 10.1 Agent Roles

V1 agent roles:

- `planner`: decomposes mission into tasks and acceptance criteria
- `implementer`: edits files and produces code artifacts
- `reviewer`: checks diffs and catches obvious issues
- `validator`: runs tests and validates success criteria
- `documenter`: updates docs and operator handoff notes
- `coordinator`: optional supervisor for larger runs

### 10.2 Agent Operating Contract

Each agent loop should follow a strict contract:

1. load task
2. load bounded context
3. acquire worktree if needed
4. execute allowed tools
5. emit structured events
6. attach artifacts
7. request validation or review
8. finish with explicit status

### 10.3 Memory Strategy

Agent memory should be externalized into durable transcripts and compact summaries rather than held only in process memory.

Memory layers:

- task-local transcript
- run-level summary
- protocol messages
- artifact references

This avoids context explosion and supports recovery.

## 11. Protocol Design

### 11.1 Why Protocols Matter

Multi-agent systems become unreliable when communication is implicit. RepoPilot Harness must require structured communication between roles.

### 11.2 Message Types

Recommended protocol envelope:

- `message_id`
- `run_id`
- `task_id`
- `from_agent`
- `to_agent`
- `message_type`
- `payload`
- `created_at`

Recommended message types:

- `task.proposed`
- `task.clarification`
- `task.claimed`
- `task.blocked`
- `artifact.ready`
- `review.requested`
- `validation.requested`
- `validation.failed`
- `task.completed`
- `run.summary`

### 11.3 Delivery Model

Use a local inbox/outbox model backed by durable files or lightweight stores. This is enough for local production use and keeps debugging simple.

## 12. Worktree and Execution Model

### 12.1 Worktree Lifecycle

1. create branch/worktree per executable task
2. bind worktree to task and agent
3. perform file mutations only inside that workspace
4. collect changed files and diff summary
5. run validation jobs from that worktree
6. mark the worktree as kept, prunable, failed, or merged later

### 12.2 Execution Safety

The runtime should enforce:

- denylist or policy guard for dangerous commands
- workspace path validation before file reads and writes
- command timeout defaults
- structured logging of every shell invocation

### 12.3 Command Model

Background commands should be treated as first-class jobs instead of loose subprocess calls. That means every command has:

- owner task
- cwd
- timeout
- status
- output capture
- retry metadata

## 13. Frontend Product Design

### 13.1 Product Position

The frontend is not a toy dashboard. It is an operator console for AI-assisted engineering execution.

The UX should feel like a serious build-and-review environment, closer to a lightweight local control room than a chat app.

### 13.2 Frontend Information Architecture

Recommended primary layout:

- left navigation
  - runs
  - active board
  - tasks
  - artifacts
  - jobs
  - settings
- center workspace
  - run overview
  - task board / dependency graph
  - event timeline
- right inspection panel
  - task detail
  - logs
  - diff preview
  - files
  - review actions

### 13.3 Primary Screens

#### Mission Intake

- repo path
- mission text
- operator constraints
- execution profile
- planner preview

#### Run Board

- run status
- task columns
- dependency indicators
- agent assignment
- validation badges

#### Task Inspector

- task description
- acceptance criteria
- assigned agent
- worktree path
- artifacts
- command history
- validation results

#### Artifact Explorer

- changed files
- diff summary
- raw file preview
- generated documentation
- test output

#### System Settings

- model config
- planner mode
- validation command templates
- worktree retention policy
- safety policy

### 13.4 UX Quality Bar

The UI should be:

- responsive on laptop screens
- easy to scan during active runs
- capable of handling long logs and many tasks
- aesthetically intentional, not generic admin-template UI
- keyboard-friendly for technical operators

## 14. Persistence Strategy

### 14.1 Initial Persistence Choice

Use local file-based persistence with structured JSON and JSONL in V1.

Reason:

- restart-safe
- easy to inspect and debug
- easy to back up
- consistent with local-first product direction

### 14.2 Storage Boundaries

Suggested stores:

- `runs/`
- `tasks/`
- `jobs/`
- `events.jsonl`
- `protocol_inbox/`
- `protocol_outbox/`
- `summaries/`
- `artifacts/`
- `transcripts/`

Wrap these behind persistence adapters so they can later move to SQLite without rewriting business logic.

### 14.3 V2 Migration Path

If local scale grows, migrate stores to SQLite while keeping file-based artifacts and logs. This gives better queryability without forcing a distributed redesign.

## 15. Security and Safety Model

### 15.1 Threats to Address

- destructive shell commands
- accidental writes outside the workspace
- log leakage of secrets
- invalid path traversal
- unbounded autonomous loops

### 15.2 Controls

- command policy layer before execution
- path resolver that rejects non-project paths
- explicit per-command timeout
- operator approval gates for risky actions in later versions
- secret scrubbing in logs when possible
- hard cap on retries and self-spawn depth

## 16. Observability and Auditability

### 16.1 Event Strategy

Every important state change must emit an event. Events are not decoration; they are part of the product contract.

Event categories:

- run lifecycle
- task lifecycle
- agent lifecycle
- protocol messages
- job execution
- worktree lifecycle
- artifact publication
- operator actions

### 16.2 What the Operator Must Be Able to Replay

For any run, the operator should be able to reconstruct:

- what was requested
- what plan was generated
- what tasks were created
- who executed each task
- what commands were run
- what files changed
- what validations passed or failed
- why the run completed or failed

## 17. Testing Strategy

### 17.1 Unit Tests

Cover:

- task state machine
- dependency resolution
- protocol validation
- command safety policy
- path validation
- summary generation

### 17.2 Integration Tests

Cover:

- run creation to completion
- task assignment and retry
- worktree creation and cleanup
- job runner output capture
- websocket event streaming

### 17.3 End-to-End Tests

Cover:

- operator creates run
- planner creates task graph
- implementer edits files in worktree
- validator runs tests
- UI shows resulting artifacts and status

### 17.4 Quality Gates

Before claiming product readiness, the system must pass:

- backend unit tests
- integration tests on temp Git repos
- UI smoke tests
- manual operator workflow verification

## 18. Release Strategy

### 18.1 Phase 1: Foundation

- create product directory
- define domain entities and persistence adapters
- build task system and event bus
- build worktree manager and job runner

### 18.2 Phase 2: Runtime

- implement planner service
- implement orchestrator and agent roles
- implement protocol bus and retry rules
- add validation pipeline

### 18.3 Phase 3: Frontend Console

- build production UI shell
- add run board, task inspector, artifact viewer, and logs
- wire websocket updates and operator actions

### 18.4 Phase 4: Hardening

- improve recovery behavior
- tighten safety checks
- expand test coverage
- write operator and developer docs

## 19. Course-to-Product Mapping

This graduation project directly operationalizes the 12 lessons:

- lesson 1 -> runtime loop
- lesson 2 -> tool execution
- lesson 3 -> planning and task writing
- lesson 4 -> subagent spawning model
- lesson 5 -> role/capability loading
- lesson 6 -> context compaction and summaries
- lesson 7 -> task board and transitions
- lesson 8 -> background jobs
- lesson 9 -> agent teams
- lesson 10 -> protocol registry and message contracts
- lesson 11 -> autonomy and recovery
- lesson 12 -> worktree isolation

## 20. Recommended Key Decisions

### Decision A: Build a modular monolith first

Why:

- lower operational complexity
- easier local debugging
- faster iteration
- enough for local-first product goals

Trade-off:

- less horizontal scaling flexibility early

### Decision B: Separate product code from course code

Why:

- avoids demo debt
- preserves teaching materials
- keeps product architecture clean

Trade-off:

- some code will need to be reimplemented more cleanly instead of directly reused

### Decision C: Use file-backed persistence first

Why:

- transparent and inspectable
- easy local recovery
- low setup burden

Trade-off:

- queries become more awkward than SQLite once scale grows

### Decision D: Make worktrees and jobs first-class domain objects

Why:

- they are core to harness engineering
- they should not be hidden behind ad hoc utility functions

Trade-off:

- slightly more initial design work, much better long-term control

## 21. V1 Acceptance Criteria

The first production-credible version should satisfy all of the following:

1. A user can submit a mission from the UI.
2. The system generates a structured task graph before execution.
3. At least three agent roles can cooperate on one run.
4. At least two executable tasks can run in parallel in isolated worktrees.
5. Real file artifacts are produced in task worktrees.
6. Validation jobs run automatically and persist logs.
7. The UI shows run status, tasks, logs, files, and validation outcomes.
8. The run can be replayed after restart from persisted state.
9. Failed tasks can be retried without corrupting the run.
10. The codebase has automated tests for critical runtime behavior.

## 22. Immediate Next Step

After this architecture is accepted, implementation should begin in this order:

1. scaffold `products/repopilot_harness/`
2. define domain models and persistence interfaces
3. implement event bus, run store, task store, and worktree store
4. implement task state machine and orchestrator skeleton
5. implement production console UI shell
6. connect real execution and validation loops

