# Jackdaw Sentry Graph Memory

Read this file before touching graph schema, graph API, trace compiler semantics, or the React graph contract.

## Repo Role

- This is the canonical home for all new graph-product work.
- The private `jackdawsentry` repo may still serve or embed this graph
  temporarily, but it is not the product source of truth.
- Any graph-to-private dependency must be classified and burned down:
  `migrate`, `duplicate-minimal`, or `private-adapter`.

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

### ADR-006
- `jackdawsentry-graph` is the canonical repo for graph feature ownership.

### ADR-007
- Private-repo graph dependencies must be actively migrated out rather than
  accepted as permanent coupling.

## Guardrails

- Do not widen this repo into the private compliance dashboard.
- Do not implement new graph-first features in the private repo and sync them
  back later.
- Keep graph session/state continuity and layout quality high priority.
- Prefer state-of-the-art graph UX only when it keeps the standalone product clearer, not more coupled.
