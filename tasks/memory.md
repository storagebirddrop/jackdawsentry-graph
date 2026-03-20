# Jackdaw Sentry Graph Memory

Read this file before touching graph schema, graph API, trace compiler semantics, or the React graph contract.

## Active Decisions

### ADR-001
- Neo4j remains the canonical investigation graph.

### ADR-002
- Raw blockchain facts live in PostgreSQL event-store tables.

### ADR-003
- `src/trace_compiler/` is the semantic boundary between raw events and the investigation graph.

### ADR-004
- `ExpansionResponse v2` is the only graph API contract.

### ADR-005
- `frontend/app/` is the primary graph frontend.

## Guardrails

- Do not widen this repo into the private compliance dashboard.
- Keep graph session/state continuity and layout quality high priority.
- Prefer state-of-the-art graph UX only when it keeps the standalone product clearer, not more coupled.
