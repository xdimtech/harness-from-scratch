# harness-from-scratch

A hands-on learning project that builds an AI agent harness from scratch, step by step, using the Anthropic SDK.

---

## Project Overview

Each `agents/sXX_*.py` file is a self-contained lesson that introduces one new concept on top of the previous one — from a bare agent loop all the way to a fully autonomous multi-agent team.

---

## Directory Structure

```
.
├── agents/                         # Agent lesson scripts
│   ├── auth.py                     # Shared client factory (load_dotenv + Anthropic bootstrap)
│   ├── s01_agent_loop.py           # Lesson 01: The smallest useful agent loop
│   ├── s02_tool_use.py             # Lesson 02: Agent loop + tool dispatch
│   ├── s03_todo_write.py           # Lesson 03: Tool dispatch + todo tracking
│   ├── s04_subagent.py             # Lesson 04: Parent/child context isolation with a task tool
│   ├── s05_skill_loading.py        # Lesson 05: On-demand skill loading
│   ├── s06_context_compact.py      # Lesson 06: Three-layer context compaction
│   ├── s07_task_system.py          # Lesson 07: Task tools with terminal-visible tracing
│   ├── s08_background_tasks.py     # Lesson 08: Background command execution
│   ├── s09_agent_teams.py          # Lesson 09: Persistent teammates with JSONL inboxes
│   ├── s10_team_protocols.py       # Lesson 10: Structured request/response protocols
│   └── s11_autonomous_agents.py    # Lesson 11: Autonomous teammates that find and claim work
├── skills/                         # Loadable skill definitions (YAML/JSON)
│   ├── agent-builder               # Skill: build new agents
│   └── code-review                 # Skill: review code
├── tests/                          # Pytest test suite
│   ├── test_auth.py                # Unit tests for agents/auth.py
│   ├── test_agents_smoke.py        # Smoke tests: all agent scripts exist and compile
│   ├── test_s06_context_compact.py # Tests for context compaction logic
│   ├── test_s07_task_system.py     # Tests for task system & tracing
│   ├── test_s08_background_tasks.py# Tests for background task execution
│   ├── test_s09_agent_teams.py     # Tests for agent team primitives
│   ├── test_s10_team_protocols.py  # Tests for team protocols
│   └── test_s11_autonomous_agents.py # Tests for autonomous agents
├── requirements.txt                # Python dependencies
└── conftest.py                     # Pytest fixtures shared across tests
```

---

## Environment Setup

### 1. Required environment variables

| Variable              | Required | Description                                            |
|-----------------------|----------|--------------------------------------------------------|
| `ANTHROPIC_API_KEY`   | ✅ Yes   | Your Anthropic API key                                 |
| `MODEL_ID`            | ✅ Yes   | Model to use, e.g. `claude-opus-4-5`                  |
| `ANTHROPIC_BASE_URL`  | ❌ No    | Custom base URL (e.g. local proxy). Omit for direct API use. |

### 2. Create a `.env` file

Copy and fill in the values:

```bash
cp .env.example .env   # if .env.example exists, otherwise create .env manually
```

`.env` contents:

```
ANTHROPIC_API_KEY=sk-ant-...
MODEL_ID=claude-opus-4-5
# ANTHROPIC_BASE_URL=http://localhost:9090   # optional, for local proxy
```

> **Never commit `.env` to version control.**

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

If a `pyproject.toml` is present (after task 62e027cd is merged), install the `agents` package in editable mode instead:

```bash
pip install -e .
```

---

## Running Tests

```bash
pytest tests/ -x -q
```

To run only the auth tests:

```bash
pytest tests/test_auth.py -v
```

---

## Running a Single Example

```bash
python agents/s01_agent_loop.py
```

Each script is self-contained and can be run directly. Start with `s01` and work your way up.

---

## Architecture: `agents/auth.py`

`auth.py` is the shared client factory used by every agent module. It exposes a single function:

```python
from agents.auth import make_client

client, MODEL = make_client()
```

**What `make_client()` does:**

1. Calls `load_dotenv(override=True)` so a `.env` file always wins over stale shell exports.
2. When `ANTHROPIC_BASE_URL` is set (e.g. a local proxy or test harness), strips `ANTHROPIC_AUTH_TOKEN` from the environment to avoid credential conflicts with non-Anthropic hosts.
3. Instantiates and returns an `Anthropic` client along with the `MODEL_ID` string, so callers can unpack both in one line.

This centralises all auth boilerplate so individual agent scripts stay focused on their lesson's concept.

---

## License

MIT
