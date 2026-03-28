# PHASE4_REFACTOR_PATCH_SPEC

## SECTION 1 — EXECUTION VERDICT

`TARGET_SLICE = Session Contract`. `ATTACHED INPUTS =` `PHASE1_REALITY_MAP.md`, `PHASE2_HOSTILE_REVIEW.md`, `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`, `tasks/memory.md`, `tasks/lessons.md`, `tasks/todo.md`. `OBSERVED:` the slice is not a graph-model redesign problem; it is a session-boundary truth problem caused by persistence side effects living in the wrong layer, a fake restore contract, mounted UI/state drift, and one small API typing bug. Repair it by preserving the compiler's tracing semantics and expansion contract, extracting session persistence into a dedicated store/service, making server-backed workspace snapshots authoritative, fixing empty-state truth and mounted bridge polling, and deleting the stale code/tests that currently institutionalize false behavior.

## SECTION 2 — PRESERVE / PATCH / REFACTOR / DELETE

### Preserve

- `OBSERVED:` Keep `ExpansionResponseV2` and session-scoped expansion caching in `src/trace_compiler/compiler.py` `_expansion_cache_key`; Phase 3 did not prove an expansion contract bug.
- `OBSERVED:` Keep the current EVM directional expansion query behavior in `src/trace_compiler/compiler.py` `_expand_from_live_history`; the inbound/outbound sample showed data presence mismatch, not directional SQL failure.
- `OBSERVED:` Keep the existing ingest-status endpoint and retry model in `src/api/routers/graph.py` `get_session_ingest_status`; the problem is the post-ingest empty-state semantics, not the endpoint's existence.
- `OBSERVED:` Keep the auth fail-closed validator in `src/api/config.py` `validate_graph_auth_disabled_safety`; the failure is test/env drift, not the guardrail.

### Patch

- `OBSERVED:` Fix false durability in `src/trace_compiler/compiler.py` `TraceCompiler.create_session` by removing swallowed persistence failure behavior from the create path.
- `OBSERVED:` Fix restore lying in `src/api/routers/graph.py` `get_investigation_session`, which currently returns stubbed empty `nodes` and `edges`.
- `OBSERVED:` Fix snapshot contract drift between `src/trace_compiler/models.py`, `frontend/app/src/store/graphStore.ts`, and `frontend/app/src/workspacePersistence.ts`.
- `OBSERVED:` Fix `resolve-tx` timestamp typing in `src/api/routers/graph.py` `resolve_transaction`.
- `OBSERVED:` Fix empty-state truth in `src/trace_compiler/compiler.py` `_build_empty_state`.
- `OBSERVED:` Move bridge-hop polling into the mounted inspector path now rooted in `frontend/app/src/components/GraphInspectorPanel.tsx` `BridgeSection`.

### Refactor

- `OBSERVED:` Create a dedicated session persistence boundary under `src/services/graph_sessions.py`; session storage does not belong inside the semantic compiler boundary described in `tasks/memory.md`.
- `OBSERVED:` Make the backend the authoritative owner of restorable workspace state; local storage becomes a non-authoritative hint/prefs layer only.
- `INFERRED:` Add an explicit save-state/status path in frontend state so the UI can represent `not yet saved` and `save failed` honestly.

### Delete

- `OBSERVED:` Delete `frontend/app/src/components/BridgeHopDrawer.tsx` after mounted polling exists; it is currently the only polling owner and is unmounted.
- `OBSERVED:` Delete the fake empty top-level restore payload behavior in `src/api/routers/graph.py` `get_investigation_session`; no more `nodes: []` / `edges: []` lies.
- `OBSERVED:` Delete or replace stale stub-era tests in `tests/test_trace_compiler/test_session_endpoints.py` and the permissive false-durability test in `tests/test_trace_compiler/test_session_persistence.py`.
- `INFERRED:` Delete local full-graph workspace persistence as the primary restore mechanism; if a compatibility buffer is kept for one release, it must be explicitly non-authoritative.

## SECTION 3 — TARGET BEHAVIOR AFTER THE FIX

- `OBSERVED->TARGET:` `POST /api/v1/graph/sessions` returns `200` only after the session row and initial server snapshot are durably written; persistence failure returns `503` and no durable-looking `session_id`.
- `OBSERVED->TARGET:` `GET /api/v1/graph/sessions/{id}` returns the actual authoritative workspace snapshot from the server, not metadata plus fake empty graph arrays.
- `OBSERVED->TARGET:` `POST /api/v1/graph/sessions/{id}/snapshot` accepts the full workspace snapshot used by the frontend and returns non-`200` on write failure.
- `OBSERVED->TARGET:` Browser refresh restores from the server snapshot first; local storage may retain only `lastSessionId` and non-sensitive UI preferences, never the authoritative graph.
- `INFERRED->TARGET:` The frontend shows explicit persistence state: `saving`, `saved`, or `save_failed`; it must never imply a server save happened when it did not.
- `OBSERVED->TARGET:` A completed ingest with indexed inbound activity can no longer produce an empty-state message that implies `no known activity at all`; directionality and indexed truth must be separated.
- `OBSERVED->TARGET:` Selecting a bridge hop in the mounted inspector triggers session-scoped polling and visibly refreshes status until terminal state.
- `OBSERVED->TARGET:` `GET /api/v1/graph/resolve-tx` returns successful responses for valid DB hits without Pydantic `timestamp` validation failures.
- `INFERRED->TARGET:` Legacy sessions without a full stored workspace restore as `legacy_bootstrap` seed-only sessions with an explicit degraded-restore flag; they must not masquerade as complete restores.

## SECTION 4 — PATCH / REFACTOR DESIGN

- `Backend boundary — OBSERVED:` Add `GraphSessionStore` in `src/services/graph_sessions.py` with three methods only: `create_session(seed, workspace)`, `get_session(session_id, owner_id)`, and `save_snapshot(session_id, owner_id, workspace)`. Move DB ownership checks and JSONB snapshot persistence here; remove session persistence side effects from `src/trace_compiler/compiler.py`.
- `Compiler boundary — INFERRED:` Split current create logic so `TraceCompiler` produces a normalized session seed/root-node payload only. The compiler remains responsible for graph semantics, not session durability.
- `API contract — OBSERVED:` Replace the current snapshot-only `node_states` contract with a versioned `WorkspaceSnapshotV1` model in `src/trace_compiler/models.py`. The stored JSONB envelope must contain `schema_version`, `sessionId`, `nodes`, `edges`, `positions`, `branches`, and `workspacePreferences`, matching the current frontend export shape from `frontend/app/src/store/graphStore.ts`. Do not rename internal node/edge fields in this patch.
- `API contract — INFERRED:` Keep `GET /sessions/{id}` backward-compatible for one release by returning current metadata plus a new required `workspace` object and `restore_state` field. If top-level `nodes` and `edges` remain during compatibility, they must mirror `workspace.nodes` and `workspace.edges`, not fake empties.
- `Legacy compatibility — INFERRED:` If `graph_sessions.snapshot` is null or old `node_states`-only JSON, synthesize a root-only `WorkspaceSnapshotV1` from persisted seed/root metadata and set `restore_state="legacy_bootstrap"`. The frontend must surface this as degraded restore, not silently treat it as full fidelity.
- `Create flow — OBSERVED:` `src/api/routers/graph.py` `create_investigation_session` should orchestrate: normalize request, ask compiler for seed/root node, construct initial `WorkspaceSnapshotV1` containing the root node, persist session and snapshot via `GraphSessionStore`, then return session metadata and root node. If the store write fails, return `503`.
- `Snapshot flow — OBSERVED:` `src/api/routers/graph.py` `save_session_snapshot` should accept full `WorkspaceSnapshotV1`, validate `sessionId` matches path `session_id`, and fail loudly on write failure. No silent `200`, no partial write semantics.
- `Restore flow — OBSERVED:` `src/api/routers/graph.py` `get_investigation_session` must load the stored workspace and return it as the restore source of truth. It must not fabricate empty graph arrays.
- `Frontend restore/autosave — OBSERVED:` Replace `frontend/app/src/App.tsx` `handleRestoreWorkspace` local-only restore with server restore. Replace full-graph writes in `frontend/app/src/workspacePersistence.ts` with a lightweight `lastSessionId` plus non-sensitive preferences hint only.
- `Frontend save behavior — INFERRED:` Autosave from `frontend/app/src/components/InvestigationGraph.tsx` should post the full server snapshot on a coarse debounce of `2000ms`, only for structural changes: nodes, edges, positions, branches, and workspace preferences. Do not autosave ephemeral state such as hover, temporary drawers, or in-flight form state.
- `Frontend save truth — INFERRED:` Add explicit `sessionSaveStatus` and `lastSavedAt` handling in the graph store or a sibling session store. On save failure, show a visible unsaved warning and do not advance `lastSavedAt`.
- `Bridge polling — OBSERVED:` Add a mounted hook at `frontend/app/src/hooks/useBridgeHopPoller.ts` and call it from `frontend/app/src/components/GraphInspectorPanel.tsx` `BridgeSection`. Poll only while a bridge hop is selected and stop on terminal states. Update the selected node's bridge status through a dedicated store patch action; do not route status polling through expansion-delta logic.
- `Empty-state semantics — OBSERVED:` Patch `src/trace_compiler/compiler.py` `_build_empty_state` so `known_tx_count` reflects indexed truth when indexed rows or completed ingest counts exist. Add `directional_tx_count` to the empty-state payload if needed to distinguish `no outbound activity` from `no known activity`. The empty-state message must mention directionality explicitly when one direction is empty and the other is populated.
- `Tx resolve typing — OBSERVED:` Change `TxResolveResponse.timestamp` in `src/trace_compiler/models.py` to `datetime | None` and return timezone-aware datetimes from `src/api/routers/graph.py` `resolve_transaction`. The JSON wire remains ISO 8601; the OpenAPI schema becomes accurate.
- `Auth/test boundary — OBSERVED:` Keep runtime auth behavior intact, but remove the global `DEBUG=true` override from `tests/conftest.py`. Replace it with explicit auth-enabled and auth-disabled fixtures so test expectations match real router inclusion in `src/api/graph_app.py`.

## SECTION 5 — TEST STRATEGY

- `Unit tests — OBSERVED:` Add store-level tests for `GraphSessionStore.create_session`, `get_session`, and `save_snapshot` covering successful write, ownership enforcement, and DB failure surfacing.
- `Unit tests — OBSERVED:` Add compiler tests for `_build_empty_state` covering completed-ingest plus inbound-only and outbound-only cases, and verify `known_tx_count` / `directional_tx_count` messaging honesty.
- `Unit tests — OBSERVED:` Add a `resolve-tx` serialization test covering DB-hit timestamp typing.
- `API tests — OBSERVED:` Replace the permissive false-durability test in `tests/test_trace_compiler/test_session_persistence.py` with assertions that create returns `503` when session persistence fails and that no restorable session exists afterward.
- `API tests — INFERRED:` Add create/restore/save contract tests proving initial root-only workspace snapshot persistence, authoritative server restore, degraded `legacy_bootstrap` restore, and snapshot save failure surfacing.
- `API tests — OBSERVED:` Replace stale stub-era session endpoint tests with a new session-contract suite that covers UUID validation, owner-bound access, auth-disabled behavior, and restore payload shape.
- `API tests — OBSERVED:` Split `tests/test_api/test_graph_app.py` into explicit auth-enabled and auth-disabled OpenAPI tests so `/api/v1/auth/login` is only required when the router is actually mounted.
- `Frontend state/render tests — INFERRED:` Add tests proving refresh restore uses the server payload, local storage retains only allowed metadata, autosave is debounced and server-backed, and save failure surfaces unsaved state.
- `Frontend state/render tests — OBSERVED:` Add a mounted bridge inspector test proving selecting a bridge hop triggers polling, updates visible status, and stops at terminal status.
- `Regression tests — OBSERVED:` Keep the existing ingest-status/retry tests and add a regression proving an address with indexed inbound activity cannot render `no known activity` on `expand_next`.
- `Performance tests — INFERRED:` Add one representative snapshot performance test with a roughly 500-node workspace to verify snapshot serialization size and that structural drag bursts do not issue per-frame writes.

## SECTION 6 — MIGRATION / ROLLOUT STRATEGY

- `Migration order — INFERRED:` Ship backend compatibility first, then frontend switch, then cleanup. Order is: store/models/router changes, then frontend restore/autosave plus bridge polling, then stale code/test deletion, then doc cleanup.
- `Compatibility — INFERRED:` Keep a one-release compatibility window where backend can read legacy `snapshot` values and normalize them to `WorkspaceSnapshotV1`. During that window, `GET /sessions/{id}` returns `restore_state` so the frontend can tell complete restore from legacy bootstrap.
- `Compatibility — INFERRED:` Allow `POST /sessions/{id}/snapshot` to accept the new `workspace` envelope immediately. If backend-first deployment is required, temporarily accept legacy `node_states` input too, but do not keep that path longer than one release.
- `Toggle strategy — INFERRED:` Do not add a new product flag unless release mechanics force staggered backend/frontend deploys. The preferred route is compatibility-by-contract, not a long-lived feature flag.
- `Rollback conditions — OBSERVED:` Roll back if create-session 5xx spikes, restore payload corruption appears, or autosave writes overwhelm the DB. In rollback, preserve backend dual-read of legacy snapshots and revert the frontend to no-authoritative-restore messaging rather than reintroducing false `saved` semantics.
- `Validation criteria — OBSERVED:` Before closeout, prove: no ghost session IDs when Postgres is unavailable, refresh restores the same graph from server snapshot, legacy rows surface `legacy_bootstrap`, bridge status updates in the mounted inspector, `resolve-tx` no longer 500s on DB hits, and auth-mode tests pass without global env cheating.
- `Out-of-slice follow-up — OBSERVED:` Cold-start docker readiness issues are real but not part of the core session-contract patch. Track compose health-gated startup as a separate follow-up unless it blocks validation.

## SECTION 7 — RISKS AND FAILURE MODES

- `INFERRED:` Snapshot payload size could create write amplification if autosave is not sufficiently debounced or if drag events are treated as continuous structural writes.
- `INFERRED:` Legacy restore normalization can still mislead users if the UI ignores `restore_state` and silently treats `legacy_bootstrap` as full restore.
- `INFERRED:` Bridge polling can leak timers or patch stale state if selection changes are not cancellation-safe.
- `OBSERVED:` Auth tests can continue masking real failures if `tests/conftest.py` keeps mutating global env instead of using scoped fixtures.
- `INFERRED:` External consumers of the current restore payload may rely on top-level `nodes` and `edges`; compatibility aliases must be correct during the transition.
- `INFERRED:` Save-failure visibility can create noisy UX if autosave retries are too aggressive; retry policy must be bounded and visible.

## SECTION 8 — TASK BREAKDOWN

1. Add `GraphSessionStore` and new workspace snapshot models, including `restore_state`.
2. Extract session creation persistence out of `TraceCompiler` and make create hard-fail on store errors.
3. Persist an initial root-only `WorkspaceSnapshotV1` during session creation.
4. Rebuild `GET /sessions/{id}` to return authoritative server workspace plus legacy normalization.
5. Rebuild `POST /sessions/{id}/snapshot` to accept full workspace payload and fail loudly on save errors.
6. Patch `TxResolveResponse.timestamp` typing and `resolve_transaction` serialization path.
7. Patch `_build_empty_state` to use indexed/directional truth and add regression coverage.
8. Switch frontend restore to server-authoritative workspace; demote local storage to `lastSessionId` plus prefs only.
9. Add frontend save-status state and coarse debounced server autosave for structural changes only.
10. Add mounted bridge polling hook in the inspector and delete `BridgeHopDrawer`.
11. Replace stale session/auth tests and remove the permissive false-durability test.
12. Update docs and memory/lessons/todo entries to match the repaired boundary and behavior.
13. Run full targeted backend and frontend regression coverage and capture validation evidence for Phase 5.

## SECTION 9 — REQUIRED DOC UPDATES

- `tasks/memory.md` must add a new durable decision stating that server-backed `WorkspaceSnapshotV1` is the authoritative session restore contract and that session persistence lives in a dedicated store/service, not inside `TraceCompiler`.
- `tasks/memory.md` must also record that mounted `GraphInspectorPanel` owns bridge-hop polling; unmounted UI paths are not feature evidence.
- `tasks/lessons.md` must add three lessons: do not bless swallowed persistence errors in tests, do not let global test env overrides hide runtime safety validators, and do not treat unmounted components as shipped behavior.
- `tasks/todo.md` must reopen or replace any `complete` items that currently overstate session restore truth or bridge polling readiness, and add the compose readiness hardening as a separate follow-up.
- `README.md` must describe server-authoritative session restore/autosave, the `legacy_bootstrap` degraded restore behavior, and the fact that local storage is now hint/prefs only.
- `SECURITY.md` must explicitly state that `GRAPH_AUTH_DISABLED` is dev-only, session ownership is mandatory in production, and browser storage must never hold bearer tokens or authoritative investigation state.
- Inline docstrings and comments in `src/api/routers/graph.py` and `src/trace_compiler/compiler.py` must delete stale `Phase 3 stub` language.
- ADR-style commentary or inline notes must document `WorkspaceSnapshotV1` compatibility handling for legacy rows.

## SECTION 10 — EXECUTION CHECKLIST

- [ ] Add `GraphSessionStore` and remove session-row writes from `TraceCompiler`.
- [ ] Define `WorkspaceSnapshotV1` and `restore_state` models.
- [ ] Make session creation return `503` on persistence failure and prove no ghost session is restorable.
- [ ] Persist initial root-only workspace snapshot on successful create.
- [ ] Make session restore return authoritative workspace data from the server.
- [ ] Normalize legacy snapshot rows to explicit `legacy_bootstrap` restores.
- [ ] Make snapshot save validate `sessionId` and fail loudly on write error.
- [ ] Patch `resolve-tx` timestamp typing so valid DB hits stop 500ing.
- [ ] Patch empty-state counts/messages so indexed/directional truth is preserved.
- [ ] Switch frontend restore off local full-graph storage.
- [ ] Limit local storage to `lastSessionId` and non-sensitive preferences only.
- [ ] Add visible `saving/saved/save_failed` state for server autosave.
- [ ] Move bridge polling into mounted inspector code and delete `BridgeHopDrawer`.
- [ ] Replace stale auth/session tests and remove the permissive persistence test.
- [ ] Update memory, lessons, todo, README, SECURITY, and stale inline comments.
- [ ] Capture validation evidence: payloads, failing-then-passing tests, and restore/bridge screenshots.

## SECTION 11 — FINAL PLAIN-ENGLISH EXECUTION READ

Engineering is not rewriting the tracer. Engineering is repairing a dishonest session boundary. Right now the backend can hand out session IDs that never really existed, the restore endpoint does not restore the graph it claims to restore, the frontend secretly relies on browser-local graph state, and bridge-hop status polling lives in dead UI code. The fix is to make the server own restorable workspace truth, make failure visible instead of swallowed, keep local storage as a hint only, patch the one bad API type, and remove the stale tests and components that currently make the wrong behavior look acceptable.

## SECTION 12 — OUTPUT FILE TO SAVE

- save this document as `PHASE4_REFACTOR_PATCH_SPEC.md`

## SECTION 13 — MANDATORY INPUTS FOR NEXT PHASE

- `PHASE1_REALITY_MAP.md`
- `PHASE2_HOSTILE_REVIEW.md`
- `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`
- `PHASE4_REFACTOR_PATCH_SPEC.md`
- implementation diff or updated code snapshot
- latest test runs and results
- rollout notes
- updated `tasks/memory.md`, `tasks/lessons.md`, `tasks/todo.md`, `README.md`, and `SECURITY.md`
- payload samples for create, restore, snapshot save, ingest status, and bridge-hop status
- screenshots showing restore behavior, save-failure state, and mounted bridge polling
- any perf/log evidence gathered during implementation

## SECTION 14 — PHASE EXIT CRITERIA

- preserve / patch / refactor / delete decisions are explicit
- target behavior is concrete and testable
- implementation order is concrete and migration-safe
- test strategy covers backend, API, frontend, and regression paths
- documentation updates are explicitly assigned
- the next-phase evidence pack is fully defined
