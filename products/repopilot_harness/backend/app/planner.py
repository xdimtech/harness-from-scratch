from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import slugify, summarize_repo


class MissionPlanner:
    def plan(
        self,
        *,
        mission: str,
        repo_root: Path,
        constraints: str,
        validation_commands: list[str],
    ) -> dict[str, Any]:
        repo = summarize_repo(repo_root)
        lowered = mission.lower()
        tokens = set(re.findall(r"[a-z0-9_+-]+", lowered))
        implementation_tracks: list[dict[str, Any]] = []

        if tokens.intersection({"frontend", "ui", "page", "web", "css", "react", "vue"}):
            implementation_tracks.append(
                {
                    "title": "Frontend delivery lane",
                    "kind": "implementation",
                    "role_required": "implementer",
                    "focus": "frontend",
                    "description": "Implement user-facing flows, UI states, and integration touchpoints for the requested capability.",
                    "acceptance_criteria": [
                        "Primary user-facing flow is implemented.",
                        "UI states are documented or scaffolded.",
                        "Touched files remain isolated to the task worktree.",
                    ],
                }
            )

        if tokens.intersection({"backend", "api", "server", "database", "worker", "auth"}):
            implementation_tracks.append(
                {
                    "title": "Backend delivery lane",
                    "kind": "implementation",
                    "role_required": "implementer",
                    "focus": "backend",
                    "description": "Implement service, data, and API changes required by the mission.",
                    "acceptance_criteria": [
                        "Core service logic is updated or scaffolded.",
                        "API/data assumptions are captured in artifacts.",
                        "Validation commands can run from the resulting worktree.",
                    ],
                }
            )

        if not implementation_tracks:
            implementation_tracks.append(
                {
                    "title": "Core implementation lane",
                    "kind": "implementation",
                    "role_required": "implementer",
                    "focus": "core",
                    "description": "Implement the primary code changes implied by the mission.",
                    "acceptance_criteria": [
                        "The requested capability has a concrete implementation path.",
                        "Artifacts describe files touched and next verification steps.",
                        "A clean validation handoff is produced.",
                    ],
                }
            )

        tasks: list[dict[str, Any]] = [
            {
                "title": "Mission framing",
                "kind": "analysis",
                "role_required": "planner",
                "description": "Capture repository context, constraints, and the execution brief before code work begins.",
                "acceptance_criteria": [
                    "Repository shape is summarized.",
                    "Mission scope and constraints are restated.",
                    "Implementation lanes have clear responsibilities.",
                ],
                "depends_on": [],
            }
        ]

        for track in implementation_tracks:
            track["depends_on"] = [1]
            tasks.append(track)

        implementation_ids = list(range(2, 2 + len(implementation_tracks)))
        validation_id = len(tasks) + 1
        tasks.append(
            {
                "title": "Validation gate",
                "kind": "validation",
                "role_required": "validator",
                "description": "Run validation commands and summarize whether the implementation lanes are safe to review.",
                "acceptance_criteria": [
                    "Configured validation commands have run.",
                    "Failures are captured with logs.",
                    "Operator can review pass/fail status per worktree.",
                ],
                "commands": validation_commands,
                "depends_on": implementation_ids,
            }
        )
        tasks.append(
            {
                "title": "Delivery handoff",
                "kind": "documentation",
                "role_required": "documenter",
                "description": "Assemble a run summary with artifacts, validations, risks, and recommended next steps.",
                "acceptance_criteria": [
                    "A run summary is published.",
                    "Artifacts and validation outcomes are linked.",
                    "Open issues and next actions are explicit.",
                ],
                "depends_on": implementation_ids + [validation_id],
            }
        )

        plan_slug = slugify(mission, fallback="mission")
        return {
            "summary": (
                "RepoPilot Harness will frame the mission, split work into isolated implementation lanes, "
                "run validations, and produce a review-ready delivery handoff."
            ),
            "repo_profile": repo,
            "plan_slug": plan_slug,
            "tasks": tasks,
            "operator_constraints": constraints,
            "validation_commands": validation_commands,
        }
