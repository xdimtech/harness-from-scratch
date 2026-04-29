# RepoPilot Harness Initial ADRs

## ADR-001: Product Code Lives Outside Course Demo Code

- Status: Accepted
- Date: 2026-04-28

### Context

The repository currently contains lesson scripts and demo-style runtime code. The graduation project requires a production-grade harness architecture with clearer module boundaries.

### Decision

Place the product implementation in `products/repopilot_harness/` rather than continuing to evolve the lesson demo entrypoint as the primary application.

### Consequences

- product code stays clean and easier to evolve
- lesson code remains useful as teaching material
- selective reuse is allowed, but only through deliberate refactoring

## ADR-002: Start with a Modular Monolith

- Status: Accepted
- Date: 2026-04-28

### Context

The harness must support multiple runtime concerns, but is initially local-first and single-operator.

### Decision

Use a modular monolith architecture with clear internal boundaries rather than microservices.

### Consequences

- simpler local operations and debugging
- lower development overhead
- later extraction remains possible if boundaries are respected

## ADR-003: Persist State in Local Files First

- Status: Accepted
- Date: 2026-04-28

### Context

The product must be inspectable, replayable, and easy to run locally without setup friction.

### Decision

Use file-backed JSON and JSONL persistence for V1, wrapped by persistence adapters.

### Consequences

- strong inspectability and low setup cost
- easier debugging and recovery
- eventual SQLite migration likely once query needs grow

## ADR-004: Treat Worktrees and Jobs as First-Class Domain Objects

- Status: Accepted
- Date: 2026-04-28

### Context

Worktree isolation and background validation are central to harness engineering, not incidental utilities.

### Decision

Model worktrees and jobs explicitly in the domain and persistence layers.

### Consequences

- clearer lifecycle management
- better UI visibility and replayability
- improved control over cleanup, retries, and diagnostics
