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

### ADR-010
- Pinned path storytelling is an analyst-memory aid. Keep it lightweight,
  derived from current graph state, and explainable from the selected node.

### ADR-011
- Protocol and primitive legends should be derived from shared frontend semantic
  metadata so node accents, legend counts, and inspector focus actions stay in
  sync.

### ADR-012
- Branch compare should summarize the currently visible investigation lens, not
  a hidden global graph state. Compare briefings must respect active route,
  semantic, and pinned-path focus.

### ADR-013
- Session briefings should export the visible graph lens as human-readable
  markdown and stay paired with JSON snapshots rather than replacing them.

### ADR-014
- The standalone local graph compose profile is graph-only. It does not imply
  live collectors or backfill are running, so empty `Prev` / `Next` expansions
  must be surfaced honestly as "no indexed activity in the current dataset."

### ADR-015
- Investigators need a first-class way to abandon the current graph and start a
  fresh search without logging out. `New Investigation` resets the canvas back
  to `SessionStarter` while keeping the authenticated browser session alive.

### ADR-016
- Live collectors and raw-event-store backfill must run as a separate ingest
  runtime, not inside the request-serving graph API. The default compose stack
  stays request-only; `docker-compose.graph.ingest.yml` is the opt-in sidecar
  overlay for ingestion.

### ADR-017
- Semantic activity detection is a near-term product priority. The public graph
  must graduate from generic service hits to first-class semantic nodes when
  chain data can support it:
  - decode EVM logs into real `swap_event` nodes
  - decode Solana instructions into real `swap_event` / instruction semantics
  - keep bridge hops rich enough to surface destination chain, destination
    asset, and destination address directly in the graph contract

### ADR-018
- Address intelligence must run in the session pipeline, not as an optional
  afterthought. Newly discovered addresses should be screened and labeled
  during session create/expand with graph-safe enrichment that can populate:
  `risk_score`, `sanctioned`, `entity_*`, and fraud / abuse labels.

### ADR-019
- Empty graph frontiers should not remain dead ends when ingestion can help.
  The near-term target is on-demand address-targeted ingest when expansion hits
  an empty frontier, while keeping the request-serving graph API isolated from
  long-running collector work.

## Guardrails

- Do not widen this repo into the private compliance dashboard.
- Do not implement new graph-first features in the private repo and sync them
  back later.
- Keep graph session/state continuity and layout quality high priority.
- Prefer state-of-the-art graph UX only when it keeps the standalone product clearer, not more coupled.
- Branch and path workflow should stay explainable to investigators. Do not add
  clever controls that hide lineage state or make the current focus ambiguous.
- Do not present generic DEX / service interactions as if they were already
  semantically decoded swaps. Until true `swap_event` generation is in place,
  keep the product honest about the distinction.
- Do not treat graph-safe enrichment as optional in the long term. The target
  product behavior is immediate screening and labeling of newly discovered
  addresses within the session flow.

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
- Near-term implementation priorities:
  - decode EVM logs and Solana instructions into real `swap_event` nodes
  - upgrade bridge handling to persist and surface destination address / asset /
    chain directly
  - run mandatory address enrichment during session create/expand so every
    discovered address is screened and labeled immediately against sanctions,
    AML / CFT, fraud, and entity datasets
  - add on-demand address-targeted ingest when expansion hits an empty frontier
  - promote DEX interactions into true `swap_event` nodes instead of generic
    `service` markers
  - add a graph-safe enrichment adapter that stamps `risk_score`,
    `sanctioned`, `entity_*`, and fraud labels onto every newly discovered
    address in the session flow
