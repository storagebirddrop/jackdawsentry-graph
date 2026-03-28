# PHASE1 Reality Map

## SECTION 1 — SCOPE OF THIS REVIEW

- OBSERVED: this merged document supersedes the earlier wrong-repo-derived pass and is grounded only in the canonical graph repo, `/home/dribble0335/dev/jackdawsentry-graph`. `README.md:41-56`, `tasks/memory.md:5-13`
- OBSERVED: the original prompt still leaves `TARGET_SLICE`, `REVIEW_GOAL`, `KNOWN_SYMPTOMS`, and `ATTACHED INPUTS` as placeholders. For this rerun, the working slice remains `Session Contract` with a correctness-risk focus.
- OBSERVED: in scope is the end-to-end session path in the standalone graph repo:
  `frontend/app/src/App.tsx`
  `frontend/app/src/components/SessionStarter.tsx`
  `frontend/app/src/components/InvestigationGraph.tsx`
  `frontend/app/src/components/GraphInspectorPanel.tsx`
  `frontend/app/src/components/BridgeHopDrawer.tsx`
  `frontend/app/src/api/client.ts`
  `frontend/app/src/hooks/useIngestPoller.ts`
  `frontend/app/src/store/graphStore.ts`
  `frontend/app/src/workspacePersistence.ts`
  `src/api/graph_app.py`
  `src/api/routers/graph.py`
  `src/api/config.py`
  `src/trace_compiler/compiler.py`
  `src/trace_compiler/models.py`
  `src/api/migrations/007_graph_sessions.sql`
  `tests/conftest.py`
  `tests/test_api/test_graph_app.py`
  `tests/test_api/test_ingest_status.py`
  `tests/test_trace_compiler/test_session_endpoints.py`
  `tests/test_trace_compiler/test_session_security.py`
  `tests/test_trace_compiler/test_session_persistence.py`
  `tests/test_trace_compiler/test_expansion_cache.py`
  `tests/test_trace_compiler/test_compiler_stub.py`
- OBSERVED: mandatory repo docs used directly in this merged pass are `README.md`, `SECURITY.md`, `tasks/memory.md`, `tasks/lessons.md`, and `tasks/todo.md`.
- OBSERVED: out of scope is full chain-compiler correctness, collector/backfill correctness, and redesign. Those are referenced only where they affect the session boundary, restore semantics, bridge-hop visibility, auth/runtime mode, or session ownership.

## SECTION 2 — WHAT THIS SLICE IS SUPPOSED TO DO

- CLAIMED ONLY: the canonical repo scope says this product owns graph session creation and restore, expansion via `ExpansionResponse v2`, bridge-hop status polling, on-demand address ingest when a frontier is empty, and the React investigation UI. `README.md:58-71`
- CLAIMED ONLY: the migration for `graph_sessions` says sessions are persisted so investigators can restore after refresh or browser crash, and that the frontend snapshot is stored in the `snapshot` JSONB column. `src/api/migrations/007_graph_sessions.sql:1-19`
- CLAIMED ONLY: the checked-in local dev posture says the graph app can run with `GRAPH_AUTH_DISABLED=true` and no login required. `README.md:242-243`, `.env:50-51`
- OBSERVED: the backend contract for this slice includes:
  `POST /api/v1/graph/sessions`
  `GET /api/v1/graph/sessions/{session_id}`
  `POST /api/v1/graph/sessions/{session_id}/snapshot`
  `POST /api/v1/graph/sessions/{session_id}/expand`
  `GET /api/v1/graph/sessions/{session_id}/hops/{hop_id}/status`
  `GET /api/v1/graph/sessions/{session_id}/ingest/status`
  `GET /api/v1/graph/resolve-tx`
  `src/api/routers/graph.py:2147-2454`
- OBSERVED: the frontend is built around that contract surface. It starts sessions, restores saved workspaces, expands nodes, auto-retries after `ingest_pending`, and exposes bridge-hop details in the inspector. `frontend/app/src/components/SessionStarter.tsx:67-124`, `frontend/app/src/App.tsx:34-45`, `frontend/app/src/components/InvestigationGraph.tsx:761-862,1541-1548,2407-2467`, `frontend/app/src/components/GraphInspectorPanel.tsx:328-335,663-797`
- INFERRED: the expected user-visible behavior is:
  start a session from a seed address
  survive refresh / browser interruption without fabricating a new graph state
  expand forward / backward / neighbor activity
  trigger and auto-retry on-demand ingest when an empty frontier can be rehydrated
  inspect bridge hops and see pending bridge state advance

## SECTION 3 — WHAT CURRENTLY OWNS THIS RESPONSIBILITY

- OBSERVED: graph-runtime auth mode, health, docs exposure, and route registration are owned by `src/api/graph_app.py`. `get_graph_runtime_user()` and `configure_auth_mode()` own the auth-disabled behavior; router inclusion happens in `create_graph_app()`. `src/api/graph_app.py:115-119,174-178,186-189,247-296`
- OBSERVED: test-time env posture is owned by `tests/conftest.py`, which forces `TESTING=true` and `DEBUG=true` before app imports. `tests/conftest.py:17-38`
- OBSERVED: session ownership and API routing are owned by `src/api/routers/graph.py`. `_get_owned_session_row()`, `_validate_expand_request()`, `create_investigation_session()`, `get_investigation_session()`, `save_session_snapshot()`, `expand_session_node()`, `get_bridge_hop_status()`, `get_session_ingest_status()`, and `resolve_transaction()` are the concrete owners. `src/api/routers/graph.py:65-121,2147-2454`
- OBSERVED: session persistence, expansion semantics, on-demand ingest triggering, cache keys, bridge-hop allowlisting, and bridge-hop DB lookup are owned by `TraceCompiler` in `src/trace_compiler/compiler.py`. `src/trace_compiler/compiler.py:138-173,281-660,905-1019`
- OBSERVED: the wire contracts are owned by `src/trace_compiler/models.py`, especially `ExpansionResponseV2`, `SessionCreateRequest`, `SessionCreateResponse`, `SessionSnapshotRequest`, `SessionSnapshotResponse`, `IngestStatusResponse`, `ExpandOptions`, `ExpandRequest`, `BridgeHopStatusResponse`, and `TxResolveResponse`. `src/trace_compiler/models.py:503-707`
- OBSERVED: frontend HTTP ownership sits in `frontend/app/src/api/client.ts`. `createSession()`, `expandNode()`, `getSessionAssets()`, `getBridgeHopStatus()`, `getIngestStatus()`, and `resolveTx()` are the client owners. `frontend/app/src/api/client.ts:91-176`
- OBSERVED: frontend session and delta state ownership sits in `frontend/app/src/store/graphStore.ts`. `initSession()`, `applyExpansionDelta()`, `exportSnapshot()`, and `importSnapshot()` own the canvas-state model. `frontend/app/src/store/graphStore.ts:191-315,405-479`
- OBSERVED: frontend local persistence ownership sits in `frontend/app/src/workspacePersistence.ts`, which stores the saved workspace in `window.localStorage`. `frontend/app/src/workspacePersistence.ts:27-59`
- OBSERVED: active bridge-hop detail rendering is owned by `GraphInspectorPanel` and its internal `BridgeSection`, not by the separate `BridgeHopDrawer`. `frontend/app/src/components/GraphInspectorPanel.tsx:328-335,663-797`, `frontend/app/src/components/InvestigationGraph.tsx:2446-2467`
- OBSERVED: bridge-hop polling logic exists in `BridgeHopDrawer`, and repo-wide search found no import or mount site for that component outside its own file. Search on 2026-03-27: `rg -n "BridgeHopDrawer" frontend/app/src` returned only `frontend/app/src/components/BridgeHopDrawer.tsx:2,22`. `frontend/app/src/components/BridgeHopDrawer.tsx:1-57`
- OBSERVED: storage dependencies are PostgreSQL `graph_sessions`, PostgreSQL `address_ingest_queue`, PostgreSQL `bridge_correlations`, Redis session cache / bridge-hop allowlists, optional Neo4j asset writes, browser `localStorage` for workspace state, and browser `sessionStorage` for bearer tokens. `src/api/migrations/007_graph_sessions.sql:1-35`, `src/api/routers/graph.py:65-121,2290-2454`, `src/trace_compiler/compiler.py:396-642,905-1019`, `frontend/app/src/workspacePersistence.ts:27-59`, `tasks/memory.md:225-230`

## SECTION 4 — CURRENT DATA / CONTROL FLOW

1. OBSERVED: bootstrap calls `/health` and checks `auth_disabled`. If auth is enabled, the frontend redirects to login when no token is present. `frontend/app/src/App.tsx:50-68`, `src/api/graph_app.py:247-257`
2. OBSERVED: the code default is `GRAPH_AUTH_DISABLED=False`, but the validator allows `GRAPH_AUTH_DISABLED=True` only when `DEBUG=True`. `src/api/config.py:240-247,410-421`
3. OBSERVED: the checked-in dev stack intentionally runs with `GRAPH_AUTH_DISABLED=true`; both the README and `.env` say so. `README.md:242-243`, `.env:50-51`
4. OBSERVED: when auth is disabled, `graph_app.configure_auth_mode()` overrides `get_current_user` with a synthetic `graph_public` analyst user, and the auth router is not mounted. `src/api/graph_app.py:174-178,294-296`
5. OBSERVED: `tests/conftest.py` forces `DEBUG=true` and `TESTING=true` before imports, so pytest does not exercise the raw shell-env path that a direct app import sees. `tests/conftest.py:17-38`
6. OBSERVED: `SessionStarter.handleSubmit()` calls `createSession()`, then immediately seeds the canvas store with the returned root node. `frontend/app/src/components/SessionStarter.tsx:67-69`, `frontend/app/src/store/graphStore.ts:192-211`
7. OBSERVED: `POST /api/v1/graph/sessions` calls `TraceCompiler.create_session(owner_user_id=str(current_user.id))`. `src/api/routers/graph.py:2239-2253`
8. OBSERVED: `TraceCompiler.create_session()` generates a session ID, root lineage, and root node, then attempts to `INSERT` into `graph_sessions`. If PostgreSQL insert fails, it logs a warning and still returns a successful `SessionCreateResponse`. `src/trace_compiler/compiler.py:281-359`
9. OBSERVED: autosave is frontend-local. `InvestigationGraph` calls `saveWorkspace(sessionId, exportSnapshot(...))`, which writes the full graph snapshot to `localStorage`. No backend snapshot call is used here. `frontend/app/src/components/InvestigationGraph.tsx:680-692`, `frontend/app/src/store/graphStore.ts:405-417`, `frontend/app/src/workspacePersistence.ts:47-54`
10. OBSERVED: restore is also frontend-local. `App.handleRestoreWorkspace()` loads `savedWorkspace.snapshot`, imports it into the store, restores workspace preferences, and sets `sessionId`. It does not call `GET /api/v1/graph/sessions/{session_id}`. `frontend/app/src/App.tsx:34-45`
11. OBSERVED: `GET /api/v1/graph/sessions/{session_id}` checks session ownership, reads `snapshot`, and returns `snapshot`, but hard-codes `nodes: []`, `edges: []`, and `branch_map: {}`. `src/api/routers/graph.py:2256-2287`
12. OBSERVED: `POST /api/v1/graph/sessions/{session_id}/snapshot` updates the `snapshot` JSONB field, but any database failure is swallowed and the endpoint still returns `snapshot_id` and `saved_at`. `src/api/routers/graph.py:2289-2327`
13. OBSERVED: the current frontend does not call the snapshot endpoint. Search on 2026-03-27 found snapshot usage only in local import/export and localStorage paths, not in `frontend/app/src/api/client.ts`. `frontend/app/src/api/client.ts:1-176`, `frontend/app/src/components/InvestigationGraph.tsx:1608-1656,2344-2353`, `frontend/app/src/App.tsx:34-45`
14. OBSERVED: `handleExpand()` calls the backend expand endpoint, which validates session ownership and unsupported controls before calling `TraceCompiler.expand()`. `frontend/app/src/components/InvestigationGraph.tsx:761-819`, `src/api/routers/graph.py:2333-2353`
15. OBSERVED: `TraceCompiler.expand()` is not a pure stub. It canonicalizes the seed node, computes a branch, checks Redis cache, dispatches into chain compilers, falls back to live history, triggers `maybe_trigger_address_ingest()` on supported empty address frontiers, returns `ingest_pending`, registers bridge hops, and caches non-empty results. `src/trace_compiler/compiler.py:378-660`
16. OBSERVED: when expansion returns empty plus `ingest_pending=true`, the frontend stores the retry payload in `ingestRetryRef`, renders `IngestPoller`, polls `/sessions/{session_id}/ingest/status`, and re-calls `handleExpand()` on completion. `frontend/app/src/components/InvestigationGraph.tsx:795-808,854-865,1541-1548`, `frontend/app/src/hooks/useIngestPoller.ts:21-64`, `src/api/routers/graph.py:2380-2432`
17. OBSERVED: bridge-hop details in the active UI come from `GraphInspectorPanel`. Node clicks set `selectedNodeId`, and the selected node is passed into the inspector. `frontend/app/src/components/InvestigationGraph.tsx:2407-2467`
18. OBSERVED: `GraphInspectorPanel.BridgeSection` shows bridge metadata and focus actions, but does not poll the bridge-hop status endpoint or trigger a refresh. `frontend/app/src/components/GraphInspectorPanel.tsx:663-797`
19. OBSERVED: `BridgeHopDrawer` does poll `/sessions/{session_id}/hops/{hop_id}/status` every 30 seconds and calls `onRefreshHop?.()` when status changes, but repo-wide search found no mounted usage of that component in the current frontend tree. `frontend/app/src/components/BridgeHopDrawer.tsx:22-57`, search evidence above
20. OBSERVED: `resolveTx()` is implemented on both sides in this repo. The frontend calls `/api/v1/graph/resolve-tx`, and the backend route resolves from event store first, then live RPC. `frontend/app/src/api/client.ts:162-176`, `src/api/routers/graph.py:2147-2235`

## SECTION 5 — OBSERVED CONTRACTS AND INVARIANTS

- OBSERVED: `ExpansionResponseV2` is the canonical graph delta contract, and in this repo it explicitly includes `ingest_pending`. `src/trace_compiler/models.py:503-551`
- OBSERVED: `IngestStatusResponse` and `TxResolveResponse` are real backend models here, not frontend inventions. `src/trace_compiler/models.py:629-659`
- OBSERVED: session ownership is enforced by `graph_sessions.created_by == str(current_user.id)` in `_get_owned_session_row()`. Missing or non-owned sessions return `404`; invalid UUIDs return `400`. `src/api/routers/graph.py:65-106`
- OBSERVED: production-safe auth defaults are present in code. `GRAPH_AUTH_DISABLED` defaults to `False`, and the settings validator rejects `GRAPH_AUTH_DISABLED=True` unless `DEBUG=True`. `src/api/config.py:240-247,410-421`
- OBSERVED: the checked-in dev stack intentionally changes that behavior. README and `.env` make auth-disabled runtime the default local dev posture. `README.md:242-243`, `.env:50-51`
- OBSERVED: test-time runtime differs from raw shell runtime because `tests/conftest.py` forces `DEBUG=true` and `TESTING=true` before imports. `tests/conftest.py:17-38`
- OBSERVED: bridge-hop polling is session-scoped by both code and docs. The router checks session ownership, `TraceCompiler.is_bridge_hop_allowed()` gates visibility by a Redis session allowlist, and `tasks/memory.md` states the same invariant. `src/api/routers/graph.py:2356-2376`, `src/trace_compiler/compiler.py:905-944`, `tasks/memory.md:225-228`
- OBSERVED: expansion guardrails are enforced in the models and router: `depth <= 3`, `max_results <= 100`, `page_size <= 50`, and unsupported `chain_filter` / `continuation_token` fail fast. `src/trace_compiler/models.py:662-675`, `src/api/routers/graph.py:109-121`, `tests/test_trace_compiler/test_session_security.py:159-280`
- OBSERVED: the expansion cache is session-scoped in live code and in active assertions. `_expansion_cache_key()` includes `session_id`, and both `test_expansion_cache_key_is_session_scoped_and_option_sensitive()` and `test_cache_key_scoped_to_session()` assert that different sessions produce different keys. `src/trace_compiler/compiler.py:138-173`, `tests/test_trace_compiler/test_compiler_stub.py:320-357`, `tests/test_trace_compiler/test_expansion_cache.py:128-145`
- OBSERVED: the header comment in `tests/test_trace_compiler/test_expansion_cache.py` still claims the cache key excludes `session_id`, but the actual tests in that same file assert the opposite. `tests/test_trace_compiler/test_expansion_cache.py:1-10,140-145`
- OBSERVED: `tasks/memory.md` and `tasks/todo.md` correctly describe features that are real in this repo: `GET /sessions/{session_id}/ingest/status`, `ingest_pending`, and the frontend auto-retry loop. `tasks/memory.md:109-117`, `tasks/todo.md:150-176`
- OBSERVED: session persistence is not fail-closed. `TraceCompiler.create_session()` swallows PostgreSQL insert failures and still returns success. `src/trace_compiler/compiler.py:334-359`, `tests/test_trace_compiler/test_session_persistence.py:64-85`
- OBSERVED: snapshot save is also not fail-closed. `save_session_snapshot()` swallows DB failures and still returns `snapshot_id` and `saved_at`. `src/api/routers/graph.py:2295-2327`
- OBSERVED: the current frontend session continuity contract is local-first. Workspace restore, snapshot import/export, and autosave all go through local JSON / `localStorage`, not the backend snapshot API. `frontend/app/src/App.tsx:34-45`, `frontend/app/src/components/InvestigationGraph.tsx:680-692,1608-1656,2344-2353`, `frontend/app/src/workspacePersistence.ts:27-59`

## SECTION 6 — CONTRADICTIONS / DRIFT / UNKNOWNS

- OBSERVED: repo posture drift from the private repo is gone here. This repo consistently describes itself as the canonical graph-product home. `README.md:41-56`, `tasks/memory.md:5-13`, `SECURITY.md:1-10`
- OBSERVED: prior wrong-repo suspicions about missing `ingest_pending`, missing `/sessions/{id}/ingest/status`, missing `/graph/resolve-tx`, and cross-session cache semantics do not hold in the canonical graph repo. Code, docs, and tests align that those features exist here and that cache keys are session-scoped. `src/api/routers/graph.py:2147-2432`, `src/trace_compiler/compiler.py:138-173`, `frontend/app/src/api/client.ts:136-176`, `tests/test_api/test_ingest_status.py:1-417`, `tests/test_trace_compiler/test_expansion_cache.py:128-145`, `tests/test_trace_compiler/test_compiler_stub.py:320-357`
- OBSERVED: session restore is still contradicted by the implementation.
  README scope says graph session creation and restore. `README.md:58-61`
  Migration 007 says sessions survive refresh/crash. `src/api/migrations/007_graph_sessions.sql:1-19`
  The backend restore route still returns empty `nodes`, empty `edges`, and empty `branch_map`. `src/api/routers/graph.py:2256-2287`
  The active UI restores from local snapshot JSON and `localStorage` instead of that route. `frontend/app/src/App.tsx:34-45`, `frontend/app/src/workspacePersistence.ts:27-59`
- OBSERVED: backend persistence semantics are weaker than the product claim.
  `create_session()` swallows DB insert failure. `src/trace_compiler/compiler.py:334-359`
  `save_session_snapshot()` swallows DB update failure. `src/api/routers/graph.py:2319-2327`
  The frontend does not call the snapshot API anyway. Search evidence on 2026-03-27 found no snapshot API usage in `frontend/app/src/api/client.ts`.
- OBSERVED: bridge-hop polling is implemented server-side and in one frontend component, but the active UI path does not appear to use it.
  README scope claims bridge-hop status polling. `README.md:60-61`
  The router and compiler implement the route. `src/api/routers/graph.py:2356-2432`, `src/trace_compiler/compiler.py:905-1019`
  `GraphInspectorPanel` is the active bridge detail surface. `frontend/app/src/components/InvestigationGraph.tsx:2446-2467`, `frontend/app/src/components/GraphInspectorPanel.tsx:328-335,663-797`
  The only visible caller of `getBridgeHopStatus()` is `BridgeHopDrawer`, and repo-wide search found no mounted usage of that component. `frontend/app/src/api/client.ts:136-143`, `frontend/app/src/components/BridgeHopDrawer.tsx:22-57`
- OBSERVED: `InvestigationGraph.tsx` still says it opens the bridge hop side drawer on `BridgeHopNode` click, but the mounted code path shown above points to the inspector, not a drawer. `frontend/app/src/components/InvestigationGraph.tsx:1-10,2407-2467`
- OBSERVED: router and compiler docstrings still describe real paths as Phase 3 stubs even though the code now performs real work. `src/api/routers/graph.py:2244-2249,2260-2264,2339-2345,2365-2370`, `src/trace_compiler/compiler.py:384-392`
- OBSERVED: `tests/test_trace_compiler/test_session_endpoints.py` is stale against the current runtime contract.
  The file header still says Phase 3 stubs and auth is enforced. `tests/test_trace_compiler/test_session_endpoints.py:1-6`
  In the focused run on 2026-03-27, five failures came from this file because it expects `401/403` or `200` on fake non-UUID session IDs, while the current runtime with auth-disabled dev settings returned `200` for unauthenticated create and `400` for invalid session IDs. Test run evidence below.
- OBSERVED: `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled` is environment-sensitive in the current dev posture. It expects `/api/v1/auth/login` in OpenAPI, but `.env` and README set `GRAPH_AUTH_DISABLED=true` for the default dev stack, and `graph_app.create_graph_app()` only mounts the auth router when auth is not disabled. `README.md:242-243`, `.env:50-51`, `src/api/graph_app.py:294-296`, `tests/test_api/test_graph_app.py:47-78`
- OBSERVED: the current shell environment is dirty enough to matter. A direct `.venv/bin/python -c "from src.api.config import settings"` probe in this checkout failed because the shell exported `DEBUG=release` while repo `.env` set `GRAPH_AUTH_DISABLED=true`; `tests/conftest.py` masks this by forcing `DEBUG=true` and `TESTING=true` before imports. Runtime probe on 2026-03-27; `tests/conftest.py:17-38`
- CLAIMED ONLY: `tasks/lessons.md` records one relevant test-maintenance pitfall: hand-rolled call-index mocks around ingest DB queries break silently when query counts change. That fits the repo’s broader stale-test pattern, but it does not by itself explain the six current failures. `tasks/lessons.md:32-41`
- UNKNOWN: whether another mounted UI path outside `frontend/app/src` actually uses `BridgeHopDrawer`, or whether bridge polling is currently dead in the canonical UI.
- UNKNOWN: whether the backend snapshot endpoint is planned for a future React wiring pass or is already relied on by another client.
- UNKNOWN: whether the checked-in dev auth-disabled `.env` is meant only for local single-user work or is also the default for shared demo/self-host paths.

## SECTION 7 — INITIAL RISK MAP

### Severe

- OBSERVED: server-side session continuity is not trustworthy end to end.
  `create_session()` can silently fail to persist the session. `src/trace_compiler/compiler.py:334-359`
  `get_investigation_session()` cannot restore graph nodes/edges. `src/api/routers/graph.py:2256-2287`
  `save_session_snapshot()` can silently fail and still report success. `src/api/routers/graph.py:2319-2327`
  The active UI masks this by relying on local snapshots. `frontend/app/src/App.tsx:34-45`, `frontend/app/src/components/InvestigationGraph.tsx:680-692,1608-1656`
  Investigator-facing risk: a session can look persistent and restorable when the server-side contract is only partially real.

- OBSERVED: bridge-hop polling appears detached from the active UI path.
  The backend route exists and the dedicated drawer polls it. `src/api/routers/graph.py:2356-2376`, `frontend/app/src/components/BridgeHopDrawer.tsx:22-57`
  The visible bridge surface is the inspector, which does not poll. `frontend/app/src/components/GraphInspectorPanel.tsx:663-797`
  Repo-wide search found no mounted usage of `BridgeHopDrawer`. Search evidence on 2026-03-27.
  Investigator-facing risk: pending bridge hops can sit on-screen without advancing even though the backend has the data path.

### Material

- OBSERVED: runtime behavior depends heavily on auth mode, and the canonical dev stack defaults to auth disabled while code defaults and security invariants describe fail-closed authenticated behavior. `src/api/config.py:240-247,410-421`, `README.md:242-243`, `.env:50-51`, `tasks/memory.md:225-229`
- OBSERVED: regression coverage for the session slice is partially stale. The focused graph-repo run on 2026-03-27 produced `66 passed, 6 failed`; five failures came from `tests/test_trace_compiler/test_session_endpoints.py`, which still encodes older auth/stub assumptions. `tests/test_trace_compiler/test_session_endpoints.py:1-114`
- OBSERVED: direct settings import in the current coexistence environment fails unless test overrides force a clean env. `tests/conftest.py:17-38` plus runtime probe on 2026-03-27

### Moderate

- OBSERVED: docstrings still frame major paths as Phase 3 stubs, which makes code review and debugging start from a false model. `src/api/routers/graph.py:2244-2249,2260-2264,2339-2345,2365-2370`, `src/trace_compiler/compiler.py:384-392`
- OBSERVED: `tests/test_expansion_cache.py` still carries a stale comment claiming cache keys exclude `session_id`, even though code and live assertions are session-scoped. `tests/test_trace_compiler/test_expansion_cache.py:1-10,140-145`, `src/trace_compiler/compiler.py:138-173`
- OBSERVED: the frontend snapshot/import terminology can mislead users into thinking backend restore exists when the active implementation is local JSON and `localStorage`. `frontend/app/src/components/InvestigationGraph.tsx:1608-1656,2344-2353`, `frontend/app/src/workspacePersistence.ts:27-59`
- CLAIMED ONLY: `tasks/lessons.md` already warns that call-index-based mocks rot as ingest query counts evolve; the repo is still paying that broader test-drift tax. `tasks/lessons.md:32-41`

### Minor

- OBSERVED: the canonical repo no longer has the split-era doc-path drift from the private repo; that earlier concern is retired here. `README.md:41-56`, `SECURITY.md:1-10`

## SECTION 8 — WHAT MUST BE INSPECTED NEXT

- OBSERVED: exact next code files to inspect:
  `frontend/app/src/components/InvestigationGraph.tsx`
  `frontend/app/src/components/GraphInspectorPanel.tsx`
  `frontend/app/src/components/BridgeHopDrawer.tsx`
  `frontend/app/src/api/client.ts`
  `frontend/app/src/workspacePersistence.ts`
  `src/api/graph_app.py`
  `src/api/routers/graph.py`
  `src/api/config.py`
  `src/trace_compiler/compiler.py`
  `src/api/migrations/007_graph_sessions.sql`
- OBSERVED: exact tests to inspect or replace next:
  `tests/conftest.py`
  `tests/test_trace_compiler/test_session_endpoints.py`
  `tests/test_api/test_graph_app.py`
  `tests/test_api/test_ingest_status.py`
  `tests/test_trace_compiler/test_session_security.py`
  `tests/test_trace_compiler/test_session_persistence.py`
  `tests/test_trace_compiler/test_expansion_cache.py`
- OBSERVED: exact runtime behaviors that must be captured next:
  real `POST /api/v1/graph/sessions` payload with PostgreSQL intentionally available and intentionally unavailable
  real `GET /api/v1/graph/sessions/{id}` payload after at least one expand and after snapshot save
  real `POST /api/v1/graph/sessions/{id}/snapshot` behavior under DB failure
  real `GET /api/v1/graph/sessions/{id}/hops/{hop_id}/status` from the current UI, including whether any request is ever fired on bridge-hop click
  real empty-frontier expand that returns `ingest_pending=true`, followed by actual auto-retry after completion
  auth-enabled versus auth-disabled OpenAPI and route surface comparison
  direct settings import in a clean shell and in the current mixed-shell setup
- OBSERVED: test-run evidence that must stay attached to the next inspection:
  `.venv/bin/python -m pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
  result: `66 passed, 6 failed`
  failure split: `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled` plus five failures in `tests/test_trace_compiler/test_session_endpoints.py`
- OBSERVED: environment/setup limitation that must be preserved with the evidence set:
  a full `requirements-test.txt` install was not usable in this Python 3.12 environment because `coincurve` failed to build due to missing `autoreconf`; the focused pytest rerun used a narrowed local `.venv` dependency set instead. Runtime setup evidence on 2026-03-27.
- OBSERVED: exact logs and probes required:
  browser network trace for bridge-hop click and pending-ingest retry
  browser screenshots proving which detail surface opens for a `bridge_hop` node
  PostgreSQL rows from `graph_sessions`, `address_ingest_queue`, and `bridge_correlations`
  Redis contents for `tc:session:{session_id}:bridge_hops`
  API logs around session create, snapshot save, expand, ingest status, and bridge status

## SECTION 9 — PRELIMINARY PLAIN-ENGLISH READ

- INFERRED: this repo is the right place to review. It already overturns several wrong-repo conclusions. `ingest_pending` is real here, `resolve-tx` is real here, and cache semantics are session-scoped.
- OBSERVED: the session contract is still weaker than the product story. The server can return a usable root node without persisting the session, can claim snapshot save success after a DB failure, and still cannot restore graph nodes/edges from `GET /sessions/{id}`. The UI papers over that with local JSON and `localStorage`.
- OBSERVED: bridge status polling exists in code, but it appears to live in a detached drawer component while the mounted inspector does not poll. That is the investigator-facing weakness that matters more than another stale comment.
- OBSERVED: the focused test rerun flipped the story. The six failures are not missing backend endpoints. They are stale tests plus auth-mode and env-sensitive assumptions.
- UNKNOWN: the next credible verdict depends on runtime proof, not more repo archaeology. The missing evidence is browser/network behavior around bridge polling and true server-side restore semantics under both success and failure conditions.

## SECTION 10 — OUTPUT FILE TO SAVE

- OBSERVED: save this document as `PHASE1_REALITY_MAP.md`.

## SECTION 11 — MANDATORY INPUTS FOR NEXT PHASE

- OBSERVED: attach `PHASE1_REALITY_MAP.md`.
- OBSERVED: do not treat `PHASE1_REALITY_MAP-additional.md` as a Phase 2 evidence input; it is a redirect stub only.
- OBSERVED: attach these exact code files:
  `README.md`
  `SECURITY.md`
  `.env`
  `tasks/memory.md`
  `tasks/lessons.md`
  `tasks/todo.md`
  `frontend/app/src/App.tsx`
  `frontend/app/src/components/SessionStarter.tsx`
  `frontend/app/src/components/InvestigationGraph.tsx`
  `frontend/app/src/components/GraphInspectorPanel.tsx`
  `frontend/app/src/components/BridgeHopDrawer.tsx`
  `frontend/app/src/api/client.ts`
  `frontend/app/src/hooks/useIngestPoller.ts`
  `frontend/app/src/store/graphStore.ts`
  `frontend/app/src/workspacePersistence.ts`
  `src/api/graph_app.py`
  `src/api/routers/graph.py`
  `src/api/config.py`
  `src/trace_compiler/compiler.py`
  `src/trace_compiler/models.py`
  `src/api/migrations/007_graph_sessions.sql`
- OBSERVED: attach these exact tests:
  `tests/conftest.py`
  `tests/test_api/test_graph_app.py`
  `tests/test_api/test_ingest_status.py`
  `tests/test_trace_compiler/test_session_endpoints.py`
  `tests/test_trace_compiler/test_session_security.py`
  `tests/test_trace_compiler/test_session_persistence.py`
  `tests/test_trace_compiler/test_expansion_cache.py`
  `tests/test_trace_compiler/test_compiler_stub.py`
- OBSERVED: attach these exact logs and outputs:
  the focused pytest command:
  `.venv/bin/python -m pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
  the result: `66 passed, 6 failed`
  the failure details for:
  `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled`
  `tests/test_trace_compiler/test_session_endpoints.py` five failing cases
  the direct settings-import failure observed in this shell
  the dependency-install failure showing `coincurve` could not build on Python 3.12 because `autoreconf` was missing
- OBSERVED: attach these exact screenshots / payloads next:
  browser screenshot of `Restore Saved Workspace`
  browser screenshot of a selected `bridge_hop` node in the current UI
  browser network log showing whether `GET /api/v1/graph/sessions/{id}/hops/{hop}/status` fires
  browser network log showing `ingest/status` polling and auto-retry
  `POST /api/v1/graph/sessions` and `GET /api/v1/graph/sessions/{id}` raw JSON payloads

## SECTION 12 — PHASE EXIT CRITERIA

- OBSERVED: do not move to Phase 2 unless the owning files and modules remain identified from the canonical graph repo, not from the private integration repo.
- OBSERVED: do not move to Phase 2 unless the current frontend and backend flow is mapped end to end, including local restore, expand, ingest retry, and bridge-hop detail behavior.
- OBSERVED: do not move to Phase 2 unless the major unknowns are called out explicitly, especially live bridge polling behavior and true server-side restore semantics.
- OBSERVED: do not move to Phase 2 unless contradictions remain listed plainly, especially the restore contract gap, the snapshot persistence gap, the detached bridge polling path, and the stale auth-sensitive tests.
- OBSERVED: do not move to Phase 2 unless the next-step evidence requirements are explicit, reproducible, and include the focused pytest output, runtime env probe, and required browser/network captures.
- OBSERVED: do not move to Phase 2 while `PHASE1_REALITY_MAP-additional.md` still carries unique evidence. It must remain a redirect only.
