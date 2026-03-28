# PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md

SECTION 1 — DEBUGGING VERDICT
OBSERVED: The session-contract slice is failing in multiple concrete places, not one vague place. The most important failure is truthfulness: `POST /api/v1/graph/sessions` can return a durable-looking `session_id` even when persistence failed in [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `TraceCompiler.create_session` (line 281), and the server restore path in [`src/api/routers/graph.py`](src/api/routers/graph.py) `get_investigation_session` (line 2256) never reconstructs graph state anyway. Around that core, there are two adjacent correctness defects: bridge-hop polling is implemented in an unmounted UI path, and [`src/api/routers/graph.py`](src/api/routers/graph.py) `resolve_transaction` (line 2148) crashes on valid DB hits because it feeds `datetime` objects into `TxResolveResponse.timestamp: str`. This is mostly a contract/boundary problem with one live data-shape bug and one semantic empty-state bug layered on top.

SECTION 2 — CONFIRMED ROOT CAUSES
- OBSERVED: `TraceCompiler.create_session` swallows PostgreSQL persistence failure and still returns `SessionCreateResponse`. Code: [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `TraceCompiler.create_session` (line 281) catches `Exception` around the `INSERT INTO graph_sessions` and only logs a warning before returning success. Runtime repro: with Postgres stopped, `POST /api/v1/graph/sessions` returned `200` and a fresh `session_id` `e6331ca6-df5a-4929-980a-3f70dacceae0`; after Postgres came back, `GET /api/v1/graph/sessions/e6331ca6-df5a-4929-980a-3f70dacceae0` returned `404 {"detail":"Session not found"}`. Logs: `graph_sessions: failed to persist session ...` in `docker logs jackdawsentry_graph_api` at `2026-03-27 17:57:48`.
- OBSERVED: Server-side session restore is not a graph restore. Code: [`src/api/routers/graph.py`](src/api/routers/graph.py) `get_investigation_session` (line 2256) hardcodes `"nodes": []`, `"edges": []`, and `"branch_map": {}` even when a session row exists. Runtime payload: a healthy `GET /api/v1/graph/sessions/925ae321-531d-42d6-91de-9cea5857f874` returned the seed metadata and saved `snapshot`, but always `nodes: []`, `edges: []`, `branch_map: {}`.
- OBSERVED: The mounted frontend restore/autosave path is browser-local, not server-backed. Code: [`frontend/app/src/App.tsx`](frontend/app/src/App.tsx) `handleRestoreWorkspace` (line 33) only calls [`frontend/app/src/workspacePersistence.ts`](frontend/app/src/workspacePersistence.ts) `loadSavedWorkspace` (line 27) and [`frontend/app/src/store/graphStore.ts`](frontend/app/src/store/graphStore.ts) `importSnapshot` (line 421). Autosave path: [`frontend/app/src/components/InvestigationGraph.tsx`](frontend/app/src/components/InvestigationGraph.tsx) line 690 calls `saveWorkspace(sessionId, exportSnapshot(...))`; storage owner: [`frontend/app/src/workspacePersistence.ts`](frontend/app/src/workspacePersistence.ts) `saveWorkspace` (line 47) writes to `window.localStorage`. The server snapshot endpoint is not part of the mounted UI restore loop.
- OBSERVED: The frontend snapshot format and backend snapshot format are different contracts. Frontend export: [`frontend/app/src/store/graphStore.ts`](frontend/app/src/store/graphStore.ts) `exportSnapshot` (line 406) writes `sessionId`, full `nodes`, full `edges`, `positions`, `branches`, and `workspacePreferences`. Backend snapshot contract: [`src/trace_compiler/models.py`](src/trace_compiler/models.py) `SessionSnapshotRequest` only accepts `node_states: List[NodeStateSnapshot]`, and [`src/api/routers/graph.py`](src/api/routers/graph.py) `save_session_snapshot` (line 2290) only persists that list into `graph_sessions.snapshot`. This is direct contract drift, not a UI misunderstanding.
- OBSERVED: Empty-state semantics can lie after ingest completes. Runtime repro: `expand_next` on session `925ae321-531d-42d6-91de-9cea5857f874` returned `ingest_pending: true`; `GET /api/v1/graph/sessions/{id}/ingest/status?...` later returned `status: completed` with `tx_count: 686`; PostgreSQL showed `address_ingest_queue.status='completed'` and `raw_transactions` contained `186` rows involving the address. Yet the retry `expand_next` response returned `empty_state.reason="live_lookup_returned_empty"`, `observed_on_chain=true`, and `known_tx_count=0`. Code root cause: [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `TraceCompiler._build_empty_state` (line 825) sources `known_tx_count` only from `client.get_address_info(address).transaction_count`, not from `address_ingest_queue` or `raw_transactions`.
- OBSERVED: The same ingest repro showed the expansion query itself was not the failing component in that case. PostgreSQL confirmed `outbound_count=0` and `inbound_count=186` for `0x1111111111111111111111111111111111111111`; `expand_next` stayed empty, but `expand_prev` returned concrete `added_nodes` from `raw_transactions`. Code path: [`src/trace_compiler/chains/evm.py`](src/trace_compiler/chains/evm.py) `EVMChainCompiler.expand_next` (line 179) and `expand_prev` (line 228), using [`src/trace_compiler/chains/_transfer_base.py`](src/trace_compiler/chains/_transfer_base.py) `_fetch_outbound_event_store` (line 283) and `_fetch_inbound_event_store` (line 346). This matters because the semantic lie is in the empty-state messaging, not in this specific directional query.
- OBSERVED: Bridge-hop polling is implemented in a component that is not mounted anywhere in the active UI path. Polling logic lives in [`frontend/app/src/components/BridgeHopDrawer.tsx`](frontend/app/src/components/BridgeHopDrawer.tsx) `BridgeHopDrawer` (line 22) and `poll()` (line 38), which calls `getBridgeHopStatus()` every 30 seconds. Repo-wide search in `frontend/app/src/**/*.tsx` found `BridgeHopDrawer` only in its own file. The mounted path is [`frontend/app/src/components/InvestigationGraph.tsx`](frontend/app/src/components/InvestigationGraph.tsx), which renders [`frontend/app/src/components/GraphInspectorPanel.tsx`](frontend/app/src/components/GraphInspectorPanel.tsx); bridge detail there is `BridgeSection` (line 663), which renders status text but does not poll.
- OBSERVED: `/api/v1/graph/resolve-tx` throws `500` on a valid event-store hit because the router returns a `datetime` into a response field typed as `str`. Runtime repro: `GET /api/v1/graph/resolve-tx?chain=ethereum&tx=0xf2fc65ce2b28bd8636c375447b1746f54460da5017918992486068d87ecc56b6` returned `500 Internal Server Error`. Logs show `ValidationError: TxResolveResponse.timestamp Input should be a valid string`. Code: [`src/api/routers/graph.py`](src/api/routers/graph.py) `resolve_transaction` (line 2148) returns `timestamp=row["timestamp"]` and `timestamp=tx_obj.timestamp`, while [`src/trace_compiler/models.py`](src/trace_compiler/models.py) `TxResolveResponse.timestamp` is `Optional[str]`.
- OBSERVED: Current tests institutionalize or mask some of these failures. [`tests/test_trace_compiler/test_session_persistence.py`](tests/test_trace_compiler/test_session_persistence.py) `test_create_session_returns_valid_response_on_pg_failure` (line 69) explicitly blesses the false-durability behavior. [`tests/conftest.py`](tests/conftest.py) line 22 overrides env vars before imports and forces `DEBUG=true`, which masks some auth/config behavior. [`tests/test_api/test_graph_app.py`](tests/test_api/test_graph_app.py) `test_graph_app_openapi_can_be_enabled` (line 53) expects `/api/v1/auth/login`, but [`src/api/graph_app.py`](src/api/graph_app.py) `create_graph_app` excludes the auth router when `settings.GRAPH_AUTH_DISABLED` is true (lines 294-295).

SECTION 3 — HIGH-CONFIDENCE PROBABLE ROOT CAUSES
- INFERRED: `save_session_snapshot` can still return `200` after an update failure if ownership lookup succeeds but the subsequent `UPDATE graph_sessions` fails. Code: [`src/api/routers/graph.py`](src/api/routers/graph.py) `save_session_snapshot` (line 2290) does `_get_owned_session_row(...)` first, then catches exceptions around the `UPDATE`, logs a warning, and still returns `SessionSnapshotResponse`. I did not fully prove this at runtime because the outage repro failed earlier during `_get_owned_session_row` and returned `503` before the `UPDATE` branch executed.
- INFERRED: Cold-start 502s are caused by hard startup dependency failures plus compose readiness drift, not by a bad request path. Runtime logs showed `graph_app.lifespan -> init_databases -> init_postgres/init_neo4j` failing until Postgres and then Neo4j became reachable; `curl http://localhost:8081/health` returned `502` until the API was manually restarted. Wiring: [`src/api/graph_app.py`](src/api/graph_app.py) `lifespan` (line 127), [`src/api/database.py`](src/api/database.py) `init_databases` / `init_postgres` / `init_neo4j`, and [`docker-compose.graph.yml`](docker-compose.graph.yml) `depends_on` without health conditions for `graph-api`.
- INFERRED: The active bridge UI will not surface hop completion in real time even if the backend status endpoint works, because the only polling component is unmounted and the mounted inspector has no refresh trigger. This is strongly implied by the code path above, but I do not have browser network traces proving the user-visible stale state in this environment because Playwright could not launch a browser here.

SECTION 4 — OPEN HYPOTHESES
- INFERRED: A real emitted `bridge_hop` node may still be refreshable through some path outside the currently inspected frontend slice, but I found no such path in `frontend/app/src`. Needed evidence: browser/network trace while clicking a real `bridge_hop` node, plus API logs for `GET /sessions/{id}/hops/{hop_id}/status`.
- INFERRED: The snapshot-update false-success branch in `save_session_snapshot` still needs a controlled repro where session ownership lookup succeeds and only the `UPDATE` fails. Needed evidence: injected DB execute failure after `_get_owned_session_row` passes, or a targeted test using a mocked pool/connection.
- INFERRED: There may be additional response-model serialization faults beyond `resolve-tx` if other endpoints return `datetime` values into string fields. Needed evidence: endpoint-by-endpoint response-model smoke run against real payloads.
- CLAIMED ONLY: Browser-level UX claims about session restore and bridge status freshness remain visually unproven in this environment because Playwright could not launch Chrome (`browserType.launchPersistentContext: Chromium distribution 'chrome' is not found`). The code evidence is strong, but screenshot/network artifacts are still missing.

SECTION 5 — CODE-PATH ANALYSIS
1. Session creation failure entry.
   - Frontend entry: [`frontend/app/src/components/SessionStarter.tsx`](frontend/app/src/components/SessionStarter.tsx) `handleSubmit` (line 61) calls `createSession()` and then `initSession(resp.session_id, resp.root_node)` immediately.
   - HTTP client: [`frontend/app/src/api/client.ts`](frontend/app/src/api/client.ts) `createSession()` posts to `/api/v1/graph/sessions`.
   - Backend entry: [`src/api/routers/graph.py`](src/api/routers/graph.py) `create_investigation_session` (line 2239) delegates to `TraceCompiler.create_session`.
   - Failure point: [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `TraceCompiler.create_session` (line 281) catches `INSERT` failures and still returns `SessionCreateResponse`.
   - User-visible result: the UI gets a valid-looking `session_id` and root node even when there is no durable server session row.
2. Session restore mismatch.
   - Backend restore path: [`src/api/routers/graph.py`](src/api/routers/graph.py) `get_investigation_session` (line 2256) fetches the row but returns hardcoded `nodes: []`, `edges: []`, `branch_map: {}`.
   - Mounted frontend restore path: [`frontend/app/src/App.tsx`](frontend/app/src/App.tsx) `handleRestoreWorkspace` (line 33) loads browser-local snapshot text, then [`frontend/app/src/store/graphStore.ts`](frontend/app/src/store/graphStore.ts) `importSnapshot` (line 421) rebuilds the graph from that client-side JSON.
   - Autosave source: [`frontend/app/src/components/InvestigationGraph.tsx`](frontend/app/src/components/InvestigationGraph.tsx) line 690 writes `exportSnapshot(...)` to local storage via [`frontend/app/src/workspacePersistence.ts`](frontend/app/src/workspacePersistence.ts) `saveWorkspace` (line 47).
   - Net effect: the frontend never consumes the server restore endpoint in the mounted path.
3. Expand / ingest / empty-state path.
   - Entry: [`frontend/app/src/components/InvestigationGraph.tsx`](frontend/app/src/components/InvestigationGraph.tsx) `handleExpand` (line 761) calls `expandNode()`.
   - Backend: [`src/api/routers/graph.py`](src/api/routers/graph.py) `expand_session_node` (line 2333) delegates to [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `TraceCompiler.expand` (line 378).
   - Query path: `TraceCompiler.expand` dispatches to `EVMChainCompiler.expand_next` / `expand_prev` in [`src/trace_compiler/chains/evm.py`](src/trace_compiler/chains/evm.py) and shared SQL in [`src/trace_compiler/chains/_transfer_base.py`](src/trace_compiler/chains/_transfer_base.py).
   - Empty frontier path: if no rows are returned, `TraceCompiler.expand` may queue on-demand ingest, then later call `_build_empty_state` (line 825).
   - Semantic failure point: `_build_empty_state` uses live RPC address info, not the ingest queue or event-store rows, to populate `known_tx_count` and `observed_on_chain`.
4. Bridge-hop polling path.
   - Mounted UI: [`frontend/app/src/components/GraphInspectorPanel.tsx`](frontend/app/src/components/GraphInspectorPanel.tsx) `BridgeSection` (line 663) only renders existing node data.
   - Unmounted poller: [`frontend/app/src/components/BridgeHopDrawer.tsx`](frontend/app/src/components/BridgeHopDrawer.tsx) `poll()` (line 38) calls `getBridgeHopStatus()` and triggers `onRefreshHop`.
   - Missing link: `InvestigationGraph.tsx` does not import or render `BridgeHopDrawer` anywhere.
5. Resolve-tx failure path.
   - Entry: [`src/api/routers/graph.py`](src/api/routers/graph.py) `resolve_transaction` (line 2148).
   - DB lookup succeeds from `raw_transactions`.
   - Failure point: constructing `TxResolveResponse` with a `datetime` timestamp instead of a string.
6. Auth/test drift path.
   - Runtime auth mode: [`src/api/graph_app.py`](src/api/graph_app.py) `configure_auth_mode` (line 174) overrides auth with a synthetic graph user when `GRAPH_AUTH_DISABLED` is true; `create_graph_app` (line 181) excludes the auth router in that mode (lines 294-295).
   - Settings gate: [`src/api/config.py`](src/api/config.py) `validate_graph_auth_disabled_safety` (line 412) rejects `GRAPH_AUTH_DISABLED=True` unless `DEBUG=True`.
   - Test masking: [`tests/conftest.py`](tests/conftest.py) line 22 forces `DEBUG=true` before app imports.

SECTION 6 — DATA / CONTRACT FAILURE ANALYSIS
- OBSERVED: The session-create contract is lying by omission. A `session_id` currently means “we minted an ID and built a root node,” not “the session row exists and can be restored later.” The code path that should make durability true is best-effort only.
- OBSERVED: The restore contract is split in two incompatible shapes.
  - Backend shape: `GET /sessions/{id}` returns metadata plus `snapshot` as a list of `NodeStateSnapshot` records.
  - Frontend shape: `exportSnapshot()` emits full graph state (`nodes`, `edges`, `positions`, `branches`, workspace prefs).
  - Because those are not the same shape, the active UI restores only from local storage and the server endpoint cannot reconstruct the same canvas.
- OBSERVED: The empty-state contract is using the wrong evidence source for `known_tx_count`. In the live repro, ingest status and `raw_transactions` both proved activity existed, but the API still said `known_tx_count=0` because `_build_empty_state()` only trusted a live address-info lookup.
- OBSERVED: The retry-expand contract is direction-sensitive and worked correctly for the sampled data. `expand_next` stayed empty because the address had no outbound rows; `expand_prev` returned data. The bug is not “ingest completed but compiler never reads the store” in this sample. The bug is “the empty-state message under-reports what is known after ingest.”
- OBSERVED: The bridge-status contract exists server-side and is advertised in `/api/v1/status` (`bridge_status_polling: true`), but the mounted UI path does not exercise it. That is a backend/frontend contract break at the integration layer.
- OBSERVED: `resolve-tx` has a data-shape failure, not a lookup failure, for the sampled Ethereum transaction. The DB row exists; the response model construction is what crashes.

SECTION 7 — CHAIN-SPECIFIC FAILURE ANALYSIS
- OBSERVED: EVM directionality is behaving correctly in the sampled ingest case. PostgreSQL showed `outbound_count=0` and `inbound_count=186` for the address, which explains why `expand_next` remained empty while `expand_prev` produced graph nodes. This is not an EVM query-logic bug for that sample.
- OBSERVED: EVM empty-frontier semantics are still wrong. After ingest completed with `tx_count=686`, the API returned `known_tx_count=0` for the same address. That is an EVM-facing semantic bug in [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `_build_empty_state` (line 825), because the field is sourced from live address info rather than the event store or ingest queue.
- OBSERVED: EVM `resolve-tx` is broken for valid DB hits because of timestamp serialization in [`src/api/routers/graph.py`](src/api/routers/graph.py) `resolve_transaction` (line 2148).
- INFERRED: Bridge-specific backend resolution may still be fine. [`src/trace_compiler/compiler.py`](src/trace_compiler/compiler.py) `get_bridge_hop_status` (line 997) reads `bridge_correlations`, and router-level allowlisting exists in [`src/api/routers/graph.py`](src/api/routers/graph.py) `get_bridge_hop_status` (line 2358). What is not fine is the active UI attachment to that contract.
- OBSERVED: I do not have confirmed UTXO, Solana, swap-router, or Bitcoin-sidechain root causes from this debugging pass. They were not reached by the runtime repros.

SECTION 8 — REPRO AND INSTRUMENTATION PLAN
- Minimal repro: healthy session round-trip.
  - `POST /api/v1/graph/sessions` with an Ethereum seed.
  - `GET /api/v1/graph/sessions/{id}` before and after `POST /snapshot`.
  - Expected current behavior: session row exists; snapshot list persists; `nodes`/`edges` remain empty.
- Minimal repro: false durability.
  - Stop Postgres.
  - Call `POST /api/v1/graph/sessions`.
  - Restart Postgres.
  - Call `GET /api/v1/graph/sessions/{returned_id}`.
  - Expected current behavior: create returns `200`; later restore returns `404`.
- Minimal repro: ingest semantic mismatch.
  - Start a fresh session on `0x1111111111111111111111111111111111111111` / `ethereum`.
  - Call `expand_next` until `ingest_pending=true`.
  - Poll `GET /sessions/{id}/ingest/status?...` until `completed`.
  - Retry `expand_next`; then call `expand_prev`.
  - Inspect `address_ingest_queue` and `raw_transactions` counts for the same address.
- Minimal repro: resolve-tx crash.
  - Call `/api/v1/graph/resolve-tx?chain=ethereum&tx=0xf2fc65ce...`.
  - Expected current behavior: `500` and `TxResolveResponse.timestamp` validation error in API logs.
- Logging / probes to add before patching:
  - In `TraceCompiler.create_session`, log a structured `session_persisted=true/false` outcome next to the returned `session_id`.
  - In `get_investigation_session`, log whether a full graph restore is intentionally unsupported or whether graph-state reconstruction failed.
  - In `TraceCompiler.expand`, log outbound/inbound event-store row counts, token/native split, and whether empty-state fields came from RPC or indexed data.
  - In the mounted bridge inspector path, log every hop-status poll request and refresh action.
  - In `resolve_transaction`, log which path resolved the tx (`raw_transactions` vs RPC) before response-model construction.
- Assertions to add temporarily:
  - Assert that any `session_id` returned to the caller has either been persisted or is explicitly marked non-durable.
  - Assert that `TxResolveResponse.timestamp` is serialized to string before model construction.
  - Assert that bridge polling is mounted whenever a `bridge_hop` node with `status='pending'` is visible in the active inspector.
- Temporary debug hooks if needed:
  - A non-production debug header or log field for `session_persisted` on create and `snapshot_write_ok` on snapshot save.
  - A short-lived debug field showing empty-state `known_tx_count_source` (`rpc`, `event_store`, `ingest_queue`, `unknown`).

SECTION 9 — TESTS THAT MUST BE WRITTEN FIRST
- OBSERVED GAP: Replace [`tests/test_trace_compiler/test_session_persistence.py`](tests/test_trace_compiler/test_session_persistence.py) `test_create_session_returns_valid_response_on_pg_failure` with a truthfulness test. The test should fail if create returns a normal durable session without either persisting or explicitly surfacing the failure.
- OBSERVED GAP: Add an API-level restore contract test proving what `GET /sessions/{id}` is allowed to return. If full graph restore is required, the test must assert non-empty `nodes`/`edges` after a saved graph snapshot. If only UI-state restore is intended, the endpoint and UI language must be pinned to that narrower contract.
- OBSERVED GAP: Add a frontend test that proves `Restore Saved Workspace` is local-only today, or change the product contract. Target files: [`frontend/app/src/App.tsx`](frontend/app/src/App.tsx), [`frontend/app/src/workspacePersistence.ts`](frontend/app/src/workspacePersistence.ts), [`frontend/app/src/store/graphStore.ts`](frontend/app/src/store/graphStore.ts).
- OBSERVED GAP: Add an integration test for the ingest semantic mismatch: completed ingest plus indexed rows must not produce `known_tx_count=0` unless the API explicitly says that count is RPC-only.
- OBSERVED GAP: Add directionality regression tests for EVM ingest replay: same address, `expand_next` empty, `expand_prev` non-empty, and empty-state wording must remain truthful.
- OBSERVED GAP: Add a mounted-UI bridge polling test. It should fail if selecting a pending `bridge_hop` in the active inspector never issues `getBridgeHopStatus()`.
- OBSERVED GAP: Add `resolve-tx` serialization tests for both DB and RPC paths to ensure timestamps are strings.
- OBSERVED GAP: Split auth/docs tests by mode. One test should pin `GRAPH_AUTH_DISABLED=true` behavior (no auth router in OpenAPI); another should pin `GRAPH_AUTH_DISABLED=false` behavior (auth router included).
- OBSERVED GAP: Delete or rewrite the stale endpoint expectations in [`tests/test_trace_compiler/test_session_endpoints.py`](tests/test_trace_compiler/test_session_endpoints.py), which still assume auth enforcement and non-UUID fake session IDs on a graph-auth-disabled app.

SECTION 10 — WHAT THE FIX MUST ACCOMPLISH
- OBSERVED NON-NEGOTIABLE: Creating a session must never imply durability unless persistence actually succeeded. Either persistence failure becomes a hard error, or the response must carry explicit non-durable state that the UI cannot mistake for a restorable session.
- OBSERVED NON-NEGOTIABLE: There must be one truthful restore contract. Either the server owns the graph snapshot the UI needs, or the UI must stop presenting local-storage restore as if it were session restore.
- OBSERVED NON-NEGOTIABLE: Empty-frontier messaging must not understate known activity after ingest. `known_tx_count` and similar fields must come from the same truth domain the product is claiming to represent, or be omitted.
- OBSERVED NON-NEGOTIABLE: Pending bridge hops in the active UI must actually poll/refresh, or the product must stop claiming bridge status polling is an active feature in that path.
- OBSERVED NON-NEGOTIABLE: `resolve-tx` must return a valid `200` payload for an existing transaction in `raw_transactions`; response-model serialization cannot be allowed to crash the endpoint.
- OBSERVED NON-NEGOTIABLE: Tests must stop encoding the current lies. A fix is not complete if the suite still blesses silent persistence failure or mode-confused auth expectations.

SECTION 11 — FINAL PLAIN-ENGLISH READ
OBSERVED: The biggest bug here is not that one query is wrong. The biggest bug is that the system tells a cleaner story than the code can support. A user can get a perfectly normal-looking session ID that is not restorable. The app’s visible “restore” feature is actually a browser-local snapshot loader, while the backend restore endpoint does not rebuild the graph. The ingest flow can correctly discover data and still tell the user `known_tx_count=0` because that field is coming from the wrong source. Bridge status polling exists in code but lives in a drawer component that the active UI never mounts. And a valid `resolve-tx` call can still 500 because a `datetime` was handed to a string field. This is a contract-truth problem first, a boundary problem second, and a straightforward data-shape bug third.

SECTION 12 — OUTPUT FILE TO SAVE
- save this document as `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`

SECTION 13 — MANDATORY INPUTS FOR NEXT PHASE
- `PHASE1_REALITY_MAP.md`
- `PHASE2_HOSTILE_REVIEW.md`
- `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`
- exact code excerpts from:
  - `src/trace_compiler/compiler.py`
  - `src/api/routers/graph.py`
  - `src/api/graph_app.py`
  - `src/api/config.py`
  - `frontend/app/src/App.tsx`
  - `frontend/app/src/components/SessionStarter.tsx`
  - `frontend/app/src/components/InvestigationGraph.tsx`
  - `frontend/app/src/components/GraphInspectorPanel.tsx`
  - `frontend/app/src/components/BridgeHopDrawer.tsx`
  - `frontend/app/src/store/graphStore.ts`
  - `frontend/app/src/workspacePersistence.ts`
- failing tests:
  - `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled`
  - `tests/test_trace_compiler/test_session_endpoints.py`
- repro steps for:
  - healthy create/get/snapshot/get round-trip
  - Postgres-down false-durability repro
  - ingest-pending -> completed -> retry expand repro
  - `resolve-tx` 500 repro
- logs / payloads:
  - `docker logs jackdawsentry_graph_api`
  - `docker logs jackdawsentry_graph_postgres`
  - raw HTTP payloads captured in this phase for create/get/snapshot/expand/ingest-status/resolve-tx
  - PostgreSQL query outputs for `graph_sessions`, `address_ingest_queue`, and `raw_transactions`
- screenshots / browser traces if available next phase:
  - active inspector selection on a pending `bridge_hop`
  - browser network log showing whether `/hops/{hop_id}/status` fires
  - restore behavior with and without browser `localStorage`
- instrumentation notes if added:
  - session persistence outcome logs
  - empty-state count-source logs
  - bridge poll logs

SECTION 14 — PHASE EXIT CRITERIA
- confirmed causes are separated from hypotheses
- the failing code path is mapped
- the contract/data failure is explained
- repro steps are concrete
- test requirements are explicit
