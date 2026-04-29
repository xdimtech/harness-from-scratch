---
name: code-review
description: Perform structured code reviews with a focus on bugs, security, testing, and maintainability.
---

# Code Review Skill

Use this skill when the user asks you to review code, inspect a diff, or audit a codebase.

## Review priorities

1. Start with correctness and regressions.
2. Then check security and data-handling risks.
3. Look for missing tests around the changed behavior.
4. Flag maintainability issues only after the functional risks.

## Suggested workflow

1. Read the changed files or the relevant entry points.
2. Identify the behavior the code is trying to implement.
3. Compare that behavior with the actual implementation.
4. Record concrete findings with file paths and line references when possible.

## Output style

- Lead with findings, ordered by severity.
- Keep the summary brief.
- Mention residual risks or missing validation if tests were not run.

## Common things to catch

- Unsafe shell execution or path handling
- Missing error handling
- Edge cases around empty input or invalid state
- Tests that do not cover the risky branches
