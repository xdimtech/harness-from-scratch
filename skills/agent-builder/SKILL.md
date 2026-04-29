---
name: agent-builder
description: Design practical AI agents by choosing minimal tools, clean context, and on-demand knowledge.
---

# Agent Builder Skill

Use this skill when the user wants to design or extend an agent, harness, or tool-using assistant.

## Core ideas

- Keep the loop simple.
- Add tools only when the model needs a real new capability.
- Load domain knowledge on demand instead of bloating the system prompt.
- Isolate noisy subtasks with child agents when context gets messy.

## Design checklist

1. What is the agent trying to accomplish?
2. Which 3-5 tools are truly essential?
3. What knowledge should be optional instead of always present?
4. What context should stay in the main thread, and what should be isolated?

## Anti-patterns

- Too many tools at once
- Huge system prompts with unrelated instructions
- Hardcoded workflows that block model reasoning
- No visibility into which tool or skill was used

## Rule of thumb

Give the model a small set of clear capabilities, expose optional expertise through skills, and let the harness stay boring.
