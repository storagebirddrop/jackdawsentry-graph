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

### ADR-008
- Branch compare/focus is the primary comparison surface for branch-scoped
  investigation workflow.

### ADR-009
- Analyst storytelling should be derived from backend `path_id` and
  `lineage_id` metadata, not invented as separate client-only identifiers.

## Guardrails

- Do not widen this repo into the private compliance dashboard.
- Do not implement new graph-first features in the private repo and sync them
  back later.
- Keep graph session/state continuity and layout quality high priority.
- Prefer state-of-the-art graph UX only when it keeps the standalone product clearer, not more coupled.
- Branch and path workflow should stay explainable to investigators. Do not add
  clever controls that hide lineage state or make the current focus ambiguous.

## Security Invariants

- Auth must fail closed. If user lookup or auth backends fail, requests are denied.
- Graph sessions are owner-bound. Missing or non-owned session IDs return `404`.
- Bridge-hop polling is session-scoped. A hop ID is only visible after it was emitted into that session.
- Production defaults keep `/docs`, `/redoc`, `/openapi.json`, and legacy flat graph endpoints disabled.
- Proxy headers are untrusted by default. Only enable proxy trust in controlled deployments.
- Browser bearer tokens may only live in `sessionStorage` during the current wave. Do not reintroduce `localStorage`.
- Expansion guardrails are mandatory: depth <= `3`, `max_results` <= `100`, `page_size` <= `50`.
- Unsupported expansion controls must fail fast rather than silently no-op.
- Security-sensitive backend or nginx changes are not complete until the live stack is rebuilt and `python scripts/quality/live_abuse_probe.py ...` passes against the running deployment.
- Performance claims are not credible without representative graph data. Run `python scripts/quality/live_perf_probe.py ...` and record the dataset footprint before treating local timings as meaningful.
- For local performance work, `python scripts/dev/load_perf_fixture_dataset.py` is the supported way to create a deterministic high-degree plus bridge/cross-chain fixture set without relying on private-repo ingestion.
