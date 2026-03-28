# PHASE2 Hostile Review

## SECTION 1 — EXECUTIVE VERDICT

- OBSERVED VERDICT: salvageable with material fixes.

## SECTION 2 — MOST IMPORTANT FINDINGS

1. OBSERVED — Severe: the slice promises durable server-side session continuity but does not actually provide it. `TraceCompiler.create_session()` returns a valid `SessionCreateResponse` even when `graph_sessions` persistence fails, `get_investigation_session()` returns empty `nodes` and `edges`, and `save_session_snapshot()` returns success even when the database write fails. `src/trace_compiler/compiler.py` / `create_session`; `src/api/routers/graph.py` / `get_investigation_session`, `save_session_snapshot`; `tests/test_trace_compiler/test_session_persistence.py`
2. OBSERVED — Severe: bridge-hop truth exists on the backend but is not carried to the active frontend detail surface. `GraphInspectorPanel.BridgeSection` renders bridge state without polling, while `BridgeHopDrawer` contains the polling logic and appears unmounted in this checkout. `frontend/app/src/components/GraphInspectorPanel.tsx` / `BridgeSection`; `frontend/app/src/components/BridgeHopDrawer.tsx`; `frontend/app/src/components/InvestigationGraph.tsx`
3. OBSERVED — Material: the system uses the word `snapshot` for incompatible artifacts. The migration says `graph_sessions.snapshot` stores the frontend snapshot, `SessionSnapshotRequest` only carries `node_states`, and the active frontend snapshot is a full local JSON workspace containing nodes, edges, positions, branches, and workspace preferences. `src/api/migrations/007_graph_sessions.sql`; `src/trace_compiler/models.py` / `SessionSnapshotRequest`; `frontend/app/src/store/graphStore.ts` / `exportSnapshot`, `importSnapshot`
4. OBSERVED — Material: the frontend is compensating for backend restore/save defects by becoming the real owner of session continuity. `App.handleRestoreWorkspace()` and autosave use `localStorage`, not `GET /sessions/{id}` or `POST /sessions/{id}/snapshot`. `frontend/app/src/App.tsx` / `handleRestoreWorkspace`; `frontend/app/src/components/InvestigationGraph.tsx`; `frontend/app/src/workspacePersistence.ts`
5. OBSERVED — Material: auth and ownership semantics are only truly owner-bound in auth-enabled mode. In auth-disabled mode, all requests are serviced as the same synthetic `graph_public` user, which collapses per-user ownership into per-runtime ownership. `src/api/graph_app.py` / `get_graph_runtime_user`, `configure_auth_mode`; `tasks/memory.md`
6. OBSERVED — Moderate: the slice is surrounded by stale tests and stale “Phase 3 stub” language that encode a false system model. That does not create the primary defects, but it makes them easier to preserve. `src/api/routers/graph.py`; `src/trace_compiler/compiler.py`; `tests/test_trace_compiler/test_session_endpoints.py`; `tests/test_api/test_graph_app.py`
7. OBSERVED — Moderate: `ExpansionResponseV2` exposes `updated_nodes` and `removed_node_ids`, but the active frontend delta merger only uses added nodes and edges, and no active backend emitter was found in this slice. That is dead contract surface waiting to become drift. `src/trace_compiler/models.py` / `ExpansionResponseV2`; `frontend/app/src/store/graphStore.ts` / `applyExpansionDelta`; repo search on 2026-03-27

## SECTION 3 — INVESTIGATOR / PRODUCT-TRUTH FAILURES

- OBSERVED: the product story says sessions survive refresh or crash, but the active server path does not restore the graph. `get_investigation_session()` returns empty `nodes`, empty `edges`, and empty `branch_map`, while the active UI restores from browser-local workspace JSON. `src/api/routers/graph.py` / `get_investigation_session`; `src/api/migrations/007_graph_sessions.sql`; `frontend/app/src/App.tsx` / `handleRestoreWorkspace`; `frontend/app/src/workspacePersistence.ts`
- OBSERVED: session creation can lie by success. If PostgreSQL persistence fails, `create_session()` still returns a usable root node and a session ID, which invites an analyst to believe the session is durable when it may not exist server-side at all. `src/trace_compiler/compiler.py` / `create_session`; `tests/test_trace_compiler/test_session_persistence.py`
- OBSERVED: snapshot save can lie by success. `save_session_snapshot()` returns `snapshot_id` and `saved_at` even after DB failure, which means the product can claim state was saved when it was not. `src/api/routers/graph.py` / `save_session_snapshot`
- OBSERVED: the active bridge-hop detail surface can present stale pending state. The backend exposes session-scoped hop-status polling, but the mounted inspector does not use it. An analyst can therefore stare at a bridge hop that remains `pending` in the UI even after the backend has resolved it. `src/api/routers/graph.py` / `get_bridge_hop_status`; `src/trace_compiler/compiler.py` / `is_bridge_hop_allowed`, `get_bridge_hop_status`; `frontend/app/src/components/GraphInspectorPanel.tsx` / `BridgeSection`; `frontend/app/src/components/BridgeHopDrawer.tsx`
- INFERRED: local-only restore undermines trust across browser boundaries, machine boundaries, and storage resets. The current system can resurrect a stale local graph even when the server-side session is absent or empty, which means “what this session is” depends on the browser, not on a canonical backend record. Evidence: `App.handleRestoreWorkspace()`, `workspacePersistence.ts`, `get_investigation_session()`.
- OBSERVED: auth-disabled mode makes owner-bound sessions effectively shared within that runtime because every request is executed as `graph_public`. That is acceptable for a single-user local dev shell; it is misleading if anyone treats it as real multi-user ownership. `src/api/graph_app.py` / `get_graph_runtime_user`, `configure_auth_mode`; `tasks/memory.md` security invariants

## SECTION 4 — MODEL / SEMANTIC FAILURES

- OBSERVED: the slice collapses three distinct concepts into the single term `snapshot`: durable server session state, lightweight node-state persistence, and full frontend workspace export. Those are not the same object and should not be spoken about as if they are. `src/api/migrations/007_graph_sessions.sql`; `src/trace_compiler/models.py` / `SessionSnapshotRequest`; `frontend/app/src/store/graphStore.ts` / `exportSnapshot`, `importSnapshot`
- OBSERVED: the semantic meaning of “session created” is wrong at the boundary. What the backend can currently guarantee is “a root node was built,” not “a durable investigation session now exists.” Returning a real `session_id` after swallowed persistence failure blurs that distinction. `src/trace_compiler/compiler.py` / `create_session`
- OBSERVED: the semantic meaning of “session restored” is also wrong at the boundary. The route named to restore a session does not restore the graph, while the frontend restore path does not come from the server. `src/api/routers/graph.py` / `get_investigation_session`; `frontend/app/src/App.tsx` / `handleRestoreWorkspace`
- OBSERVED: the current evidence does not show the frontend inventing branch, path, or lineage identity. `pathStories` are grouped by backend-derived `path_id` and carry backend `lineage_id`; that aligns with ADR-009 and ADR-010. `frontend/app/src/components/InvestigationGraph.tsx` / `pathStories`; `tasks/memory.md` / ADR-009, ADR-010
- OBSERVED: `ExpansionResponseV2` carries a broader delta ontology than the mounted frontend actually supports. The contract includes `updated_nodes` and `removed_node_ids`, but `applyExpansionDelta()` only merges added nodes and edges. That is a semantic mismatch between the declared graph delta and the consumed graph delta. `src/trace_compiler/models.py` / `ExpansionResponseV2`; `frontend/app/src/store/graphStore.ts` / `applyExpansionDelta`
- INFERRED: the slice is not fundamentally graph-model-toxic. The lineage abstractions themselves appear defensible; the damage is coming from dishonest durability semantics and boundary compensation, not from collapsed graph ontology. Evidence: `src/trace_compiler/models.py`, `frontend/app/src/components/InvestigationGraph.tsx`, `tasks/memory.md`.

## SECTION 5 — SYSTEM-BOUNDARY FAILURES

- OBSERVED: the frontend is compensating for backend defects instead of consuming a trustworthy backend contract. Restore, autosave, and workspace continuity all live in browser storage while the backend exposes restore/save endpoints that are either partial, dishonest, or unused. `frontend/app/src/App.tsx`; `frontend/app/src/components/InvestigationGraph.tsx`; `frontend/app/src/workspacePersistence.ts`; `src/api/routers/graph.py`
- OBSERVED: persistence failures are handled in the wrong layer and with the wrong semantics. The compiler and router swallow DB failures and then emit success-shaped responses, pushing uncertainty upward while pretending the contract succeeded. `src/trace_compiler/compiler.py` / `create_session`; `src/api/routers/graph.py` / `save_session_snapshot`
- OBSERVED: polling logic for bridge-hop truth lives in the wrong frontend component. The active visual owner is `GraphInspectorPanel`, but the state-refresh logic lives in `BridgeHopDrawer`, which appears disconnected from the active UI path. `frontend/app/src/components/GraphInspectorPanel.tsx`; `frontend/app/src/components/BridgeHopDrawer.tsx`; `frontend/app/src/components/InvestigationGraph.tsx`
- OBSERVED: the route layer and compiler layer still advertise themselves as stubs even where real behavior exists. That is boundary drift: the system tells reviewers and test authors the wrong story about which layer is already responsible for real work. `src/api/routers/graph.py` / `create_investigation_session`, `get_investigation_session`, `expand_session_node`, `get_bridge_hop_status`; `src/trace_compiler/compiler.py` / `expand`
- OBSERVED: auth-disabled dev mode changes the route surface and ownership behavior, but tests and docs do not pin that mode consistently. `create_graph_app()` omits `/api/v1/auth` when auth is disabled; `tests/conftest.py` forces a different env posture; `test_graph_app_openapi_can_be_enabled` and `test_session_endpoints.py` still encode stale assumptions. `src/api/graph_app.py` / `create_graph_app`; `tests/conftest.py`; `tests/test_api/test_graph_app.py`; `tests/test_trace_compiler/test_session_endpoints.py`
- INFERRED: `graph_sessions` is currently being treated as if it were the canonical restore substrate without a coherent canonical restore contract. The table stores something called snapshot, but the active app’s real workspace state lives elsewhere. Evidence: migration 007, `SessionSnapshotRequest`, frontend export/import.

## SECTION 6 — CHAIN-SPECIFIC FAILURES OR RISKS

- OBSERVED: the primary chain-adjacent failure in this slice is bridge-hop truth propagation, not bridge decoding itself. The backend has a session-scoped hop-status route and allowlist, but the mounted UI does not appear to poll it. That makes cross-chain state resolution investigator-visible but stale. `src/api/routers/graph.py` / `get_bridge_hop_status`; `src/trace_compiler/compiler.py` / `is_bridge_hop_allowed`, `get_bridge_hop_status`; `frontend/app/src/components/GraphInspectorPanel.tsx`; `frontend/app/src/components/BridgeHopDrawer.tsx`
- OBSERVED: on-demand ingest for empty frontiers is real in this repo, and the frontend retry loop is wired. This is not one of the current architectural lies in the slice. `src/api/routers/graph.py` / `get_session_ingest_status`; `frontend/app/src/hooks/useIngestPoller.ts`; `frontend/app/src/components/InvestigationGraph.tsx` / `handleExpand`; `tests/test_api/test_ingest_status.py`
- INFERRED: the remaining risk in the ingest path is not feature absence but runtime proof. Phase 3 still needs to prove that retry after `ingest_pending` produces honest graph continuity rather than duplicate or confusing expansions. Evidence: no attached runtime network traces or screenshots yet.
- OBSERVED: `resolve-tx` is implemented on both sides in this slice, so there is no earned basis here for claiming missing tx-resolution support. `src/api/routers/graph.py` / `resolve_transaction`; `frontend/app/src/api/client.ts` / `resolveTx`
- CLAIMED ONLY: `tasks/memory.md` documents earlier tx-hash normalization fixes for UTXO and Solana, but this Phase 2 slice review did not attach new runtime evidence that those chain-specific fixes are currently regressing. `tasks/memory.md`
- UNKNOWN: there is not enough Phase 2 evidence to call Bitcoin, EVM, or Solana lineage semantics broken within this slice. The current failures are concentrated in session continuity and bridge-state surfacing, not in proven per-chain graph corruption.

## SECTION 7 — CODE / MAINTAINABILITY FAILURES

- OBSERVED: stale “Phase 3 stub” wording remains in live router and compiler paths that now do real work. That poisons future reviews and encourages stale tests. `src/api/routers/graph.py`; `src/trace_compiler/compiler.py`
- OBSERVED: `tests/test_trace_compiler/test_session_endpoints.py` is stale enough to be actively misleading. It still frames the endpoints as stub/auth-enforced surfaces and produced five failures in the focused Phase 1 run for outdated reasons. `tests/test_trace_compiler/test_session_endpoints.py`; Phase 1 focused pytest output
- OBSERVED: `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled` is environment-sensitive in a way that is not cleanly isolated from repo defaults. `tests/conftest.py` also masks direct settings-import failure by forcing `DEBUG=true`. `tests/test_api/test_graph_app.py`; `tests/conftest.py`; `src/api/config.py`; `src/api/graph_app.py`
- OBSERVED: business logic has been stranded in an apparently unmounted component. `BridgeHopDrawer` is not just dead view code; it owns the only visible polling logic for bridge-hop refresh in this slice. `frontend/app/src/components/BridgeHopDrawer.tsx`
- OBSERVED: the snapshot boundary is hard to test because the live frontend and the documented/backend snapshot path are not the same mechanism. That makes end-to-end correctness harder to assert and easier to fake. `frontend/app/src/store/graphStore.ts`; `frontend/app/src/workspacePersistence.ts`; `src/trace_compiler/models.py`; `src/api/routers/graph.py`
- OBSERVED: the contract surface is broader than the consumed surface. `updated_nodes` and `removed_node_ids` exist in the contract but not in the active frontend merger. That is maintainability debt even if it is not yet a user-visible bug. `src/trace_compiler/models.py`; `frontend/app/src/store/graphStore.ts`
- CLAIMED ONLY: `tasks/lessons.md` correctly warns that positional ingest mocks rot when query counts change. That lesson fits the repo’s stale-test pattern, but it is contextual evidence, not proof of a specific runtime defect in this slice. `tasks/lessons.md`

## SECTION 8 — WHAT IS ACTUALLY FINE

- OBSERVED: the lineage model itself is defensible. `session_id`, `branch_id`, `path_id`, and `lineage_id` are backend-derived, and the frontend path-story view is derived from those fields rather than inventing its own identity scheme. `src/trace_compiler/models.py`; `frontend/app/src/components/InvestigationGraph.tsx`; `tasks/memory.md`
- OBSERVED: expansion cache scoping is correct in the canonical repo. `_expansion_cache_key()` includes `session_id`, and active tests assert session isolation. `src/trace_compiler/compiler.py`; `tests/test_trace_compiler/test_expansion_cache.py`; `tests/test_trace_compiler/test_compiler_stub.py`
- OBSERVED: on-demand ingest support is real and not imaginary frontend semantics. `IngestStatusResponse`, `/sessions/{id}/ingest/status`, `ingest_pending`, and the retry loop all exist in code and tests. `src/trace_compiler/models.py`; `src/api/routers/graph.py`; `frontend/app/src/hooks/useIngestPoller.ts`; `tests/test_api/test_ingest_status.py`
- OBSERVED: tx-hash resolution support is real in this repo. `resolve_transaction()` and `resolveTx()` exist and are not the current failure center. `src/api/routers/graph.py`; `frontend/app/src/api/client.ts`
- OBSERVED: several guardrails are real, not decorative: session ownership checks, session-scoped bridge-hop allowlisting, expansion depth/result limits, and unsupported-control fail-fast behavior. `src/api/routers/graph.py`; `src/trace_compiler/models.py`; `tests/test_trace_compiler/test_session_security.py`; `tasks/memory.md`
- OBSERVED: the code defaults still aim fail-closed on auth. The dangerous behavior is the checked-in auth-disabled dev posture and its masking effects, not the absence of a protective default in config. `src/api/config.py`; `src/api/graph_app.py`
- INFERRED: this slice should not be rewritten from scratch. The durable identity model, ingest support, tx-resolve support, and core guardrails are usable foundations once the restore/save/bridge boundary lies are fixed.

## SECTION 9 — WHAT MUST BE PROVEN IN DEBUGGING

1. OBSERVED: prove whether `TraceCompiler.create_session()` can still return a durable-looking `session_id` after intentional PostgreSQL failure, and capture the exact UI behavior on refresh afterward. Instrument `src/trace_compiler/compiler.py` / `create_session`, `src/api/routers/graph.py` / `create_investigation_session`, and the network/UI flow in `SessionStarter`.
2. OBSERVED: prove whether `POST /api/v1/graph/sessions/{session_id}/snapshot` returns success on DB failure in a live run, and capture whether any current UI path ever calls it. Instrument `src/api/routers/graph.py` / `save_session_snapshot`, browser network traces, and API logs.
3. OBSERVED: prove what `GET /api/v1/graph/sessions/{session_id}` returns after real expands and after local autosave, and whether it is usable for restore in any realistic scenario. Instrument `src/api/routers/graph.py` / `get_investigation_session` and capture raw JSON payloads.
4. OBSERVED: prove whether the mounted bridge-hop UI ever fires `GET /sessions/{id}/hops/{hop_id}/status` when a bridge hop is selected. Instrument browser network logs, `GraphInspectorPanel`, and `InvestigationGraph` selection flow.
5. OBSERVED: prove whether a backend-resolved hop changes the active inspector without manual refresh or re-expand. Instrument `GraphInspectorPanel.BridgeSection`, `BridgeHopDrawer`, and any bridge-node refresh path.
6. OBSERVED: prove how auth-enabled and auth-disabled modes change ownership and route surface, including whether multiple browser sessions share the same synthetic owner in auth-disabled mode. Instrument `src/api/graph_app.py` / `get_graph_runtime_user`, `configure_auth_mode`, `create_graph_app`, plus two-session repro notes.
7. OBSERVED: prove whether `updated_nodes` or `removed_node_ids` are ever emitted by the backend in the session-contract slice. If they are not, document them as dead surface; if they are, prove the frontend currently lies by omission. Instrument compiler outputs and `graphStore.applyExpansionDelta()`.
8. OBSERVED: reproduce the env-loading hazard directly: shell `DEBUG=release` plus repo `.env` `GRAPH_AUTH_DISABLED=true`, and compare that with the pytest environment forced by `tests/conftest.py`. Capture exact failure output.
9. INFERRED: prove that ingest-pending retry does not create duplicate or semantically confusing path/branch presentation after data arrives. That is a runtime continuity question, not yet a confirmed bug.

## SECTION 10 — FINAL PLAIN-ENGLISH VERDICT

- INFERRED: this slice is mostly a contract problem and a boundary problem, with a smaller maintainability problem wrapped around it. The underlying lineage abstractions do not currently look dishonest. The system is failing because it promises durable session continuity and live bridge-hop truth at the product boundary while the active implementation relies on browser-local state and a detached polling path.
- OBSERVED: the worst defects are not “the graph model is wrong.” The worst defects are “the product says the server owns session continuity when it mostly does not” and “the backend can know a bridge hop resolved while the mounted UI stays stale.” `src/trace_compiler/compiler.py`; `src/api/routers/graph.py`; `frontend/app/src/App.tsx`; `frontend/app/src/components/GraphInspectorPanel.tsx`; `frontend/app/src/components/BridgeHopDrawer.tsx`
- INFERRED: that makes the slice salvageable, not conceptually doomed. Fixing it requires making the backend session contract truthful, making the active bridge surface consume the real hop-status path, and deleting or repairing stale tests and stale semantics that keep lying about what the system already is.

## SECTION 11 — OUTPUT FILE TO SAVE

- OBSERVED: save this document as `PHASE2_HOSTILE_REVIEW.md`.

## SECTION 12 — MANDATORY INPUTS FOR NEXT PHASE

- OBSERVED: attach `PHASE1_REALITY_MAP.md`.
- OBSERVED: attach `PHASE2_HOSTILE_REVIEW.md`.
- OBSERVED: attach the failing-test output from the focused run that produced `66 passed, 6 failed`, especially:
  `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled`
  `tests/test_trace_compiler/test_session_endpoints.py` failing cases
- OBSERVED: attach runtime logs for:
  session creation with PostgreSQL available and unavailable
  snapshot save with PostgreSQL available and unavailable
  bridge-hop status polling attempts from the active UI
  auth-enabled vs auth-disabled app boot and OpenAPI surface
- OBSERVED: attach screenshots and payloads for:
  `Restore Saved Workspace`
  a selected `bridge_hop` node in the active inspector
  raw `POST /api/v1/graph/sessions` response
  raw `GET /api/v1/graph/sessions/{id}` response after expand
  raw `GET /api/v1/graph/sessions/{id}/hops/{hop_id}/status` response
  raw `GET /api/v1/graph/sessions/{id}/ingest/status` polling sequence
- OBSERVED: attach exact code files to instrument:
  `src/trace_compiler/compiler.py`
  `src/api/routers/graph.py`
  `src/api/graph_app.py`
  `src/api/config.py`
  `frontend/app/src/App.tsx`
  `frontend/app/src/components/InvestigationGraph.tsx`
  `frontend/app/src/components/GraphInspectorPanel.tsx`
  `frontend/app/src/components/BridgeHopDrawer.tsx`
  `frontend/app/src/store/graphStore.ts`
  `frontend/app/src/workspacePersistence.ts`
  `tests/conftest.py`
  `tests/test_api/test_graph_app.py`
  `tests/test_trace_compiler/test_session_endpoints.py`
  `tests/test_trace_compiler/test_session_persistence.py`
- OBSERVED: attach repro notes for:
  mixed-shell env failure on direct settings import
  two-browser or two-user auth-disabled ownership behavior
  bridge-hop click flow and whether status polling actually fires
  restore flow after refresh with and without local browser storage
- OBSERVED: attach DB/Redis inspection outputs for:
  `graph_sessions`
  `address_ingest_queue`
  `bridge_correlations`
  `tc:session:{session_id}:bridge_hops`

## SECTION 13 — PHASE EXIT CRITERIA

- OBSERVED: do not move to Phase 3 unless the verdict remains explicit and one of the four allowed values.
- OBSERVED: do not move to Phase 3 unless the top failure classes are ranked by severity rather than buried in prose.
- OBSERVED: do not move to Phase 3 unless investigator-truth risks are called out plainly, especially false session durability and stale bridge-hop status.
- OBSERVED: do not move to Phase 3 unless the debugging targets are concrete enough to instrument without inventing new questions.
- OBSERVED: do not move to Phase 3 unless the next evidence pack is explicit, including failing tests, runtime logs, screenshots/payloads, code files to instrument, and repro notes.
