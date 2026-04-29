from __future__ import annotations

from typing import Iterable

RUN_STATUSES = {
    "draft",
    "planned",
    "approved",
    "running",
    "paused",
    "failed",
    "completed",
    "archived",
}

TASK_STATUSES = {
    "todo",
    "ready",
    "claimed",
    "executing",
    "validating",
    "blocked",
    "review_required",
    "completed",
    "failed",
    "canceled",
}

TERMINAL_TASK_STATUSES = {"completed", "failed", "canceled"}


def validate_run_status(status: str) -> str:
    if status not in RUN_STATUSES:
        raise ValueError(f"Invalid run status: {status}")
    return status


def validate_task_status(status: str) -> str:
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid task status: {status}")
    return status


def dependencies_satisfied(dep_statuses: Iterable[str]) -> bool:
    statuses = list(dep_statuses)
    return bool(statuses) and all(status == "completed" for status in statuses)


def next_task_status(current: str, target: str) -> str:
    validate_task_status(current)
    validate_task_status(target)
    transitions = {
        "todo": {"ready", "canceled"},
        "ready": {"claimed", "canceled", "blocked"},
        "claimed": {"executing", "blocked", "failed"},
        "executing": {"validating", "review_required", "completed", "failed", "blocked"},
        "validating": {"completed", "failed", "review_required"},
        "review_required": {"completed", "failed", "ready"},
        "blocked": {"ready", "failed", "canceled"},
        "completed": set(),
        "failed": {"ready"},
        "canceled": set(),
    }
    if target not in transitions[current]:
        raise ValueError(f"Illegal task transition: {current} -> {target}")
    return target
