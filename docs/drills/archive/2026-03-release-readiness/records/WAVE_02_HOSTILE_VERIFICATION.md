# Wave 02 Hostile Verification

## Claimed Objective

- Claimed in `WAVE_02_IMPLEMENTATION.md`: Wave 02 made backend session state the real restore/autosave authority by:
  1. persisting an initial workspace snapshot at create time,
  2. restoring from `GET /sessions/{id}`,
  3. autosaving to `POST /sessions/{id}/snapshot`.

## Files Reviewed

- `WAVE_02_IMPLEMENTATION.md`
- `MASTER_EXECUTION_PLAN_UPDATED.md`
- `NEXT_WAVE_HANDOFF.md`
- `tasks/memory.md`
- `tasks/lessons.md`
- `tasks/todo.md`
- `src/trace_compiler/compiler.py`
- `src/api/routers/graph.py`
- `src/services/graph_sessions.py`
- `frontend/app/src/App.tsx`
- `frontend/app/src/components/SessionStarter.tsx`
- `frontend/app/src/components/InvestigationGraph.tsx`
- `frontend/app/src/api/client.ts`
- `frontend/app/src/workspacePersistence.ts`
- `frontend/app/src/types/graph.ts`
- `tests/test_trace_compiler/test_session_persistence.py`
- `tests/test_trace_compiler/test_session_endpoints.py`
- `frontend/app/package.json`

## Verified Behaviors

- Observed in `src/trace_compiler/compiler.py:create_session`: initial session creation now inserts both `snapshot` and `snapshot_saved_at` into `graph_sessions`, and the inserted snapshot is a root-only `WorkspaceSnapshotV1`.
- Observed in `tests/test_trace_compiler/test_session_persistence.py:test_create_session_persists_initial_workspace_snapshot`: there is direct regression coverage that the create-session SQL call now includes a serialized workspace snapshot and `snapshot_saved_at`.
- Observed in `src/api/routers/graph.py:get_investigation_session`: backend restore now returns `workspace`, `restore_state`, and top-level mirrored `nodes` / `edges` instead of fake empty arrays.
- Observed in `src/services/graph_sessions.py:normalize_workspace`: backend restore normalizes both full stored snapshots and legacy rows.
- Observed in `tests/test_trace_compiler/test_session_endpoints.py:test_returns_full_workspace_when_snapshot_is_v1`: backend restore of a v1 workspace snapshot is covered.
- Observed in `frontend/app/src/App.tsx:handleRestoreWorkspace`: the restore path now calls `getSession(savedWorkspace.sessionId)` and imports `response.workspace`.
- Observed in `frontend/app/src/api/client.ts:getSession`: the frontend has a dedicated `GET /api/v1/graph/sessions/{id}` client.
- Observed in `frontend/app/src/components/InvestigationGraph.tsx`: autosave now calls `saveSessionSnapshot(sessionId, snapshotPayload)` after a debounce.
- Observed in `frontend/app/src/api/client.ts:saveSessionSnapshot`: the frontend has a dedicated `POST /api/v1/graph/sessions/{id}/snapshot` client.
- Observed in `frontend/app/src/workspacePersistence.ts:saveWorkspace`: newly written browser storage no longer stores the graph snapshot payload; it stores a session reference and timestamp only.

## Unverified Behaviors

- Observed in `frontend/app/package.json`: there is no frontend test script or frontend runtime test harness in this repo. The Wave 02 validation did not actually prove that a real user click on “Restore last workspace” issues a live `GET /sessions/{id}` request and lands correctly on the canvas.
- Observed in the test suite: no test exercises `frontend/app/src/App.tsx:handleRestoreWorkspace`.
- Observed in the test suite: no test exercises `frontend/app/src/components/InvestigationGraph.tsx` autosave behavior or proves that `POST /sessions/{id}/snapshot` is actually emitted from the running UI.
- Observed in the test suite: no end-to-end test covers create-session followed by backend restore of that newly created session through the real API.
- Observed in the test suite: no test proves that save failures in the frontend change the visible toolbar state correctly or recover correctly on the next successful save.
- Observed in the codebase: `restore_state` exists in the backend and frontend types, but there is no UI handling that surfaces `legacy_bootstrap` to the investigator.

## Contradictions Found

- Observed in `frontend/app/src/App.tsx` and `frontend/app/src/components/SessionStarter.tsx`: backend session state is now the authority for workspace content, but the UI still depends on local storage to remember which session is restorable. If local storage is missing, the backend session exists but the restore affordance disappears. That is not full server-authoritative recovery.
- Observed in `frontend/app/src/App.tsx:handleRestoreWorkspace`: the code ignores `response.restore_state`. A degraded backend restore (`legacy_bootstrap`) is imported and presented like a normal restore. That contradicts the backend honesty contract added in Wave 02.
- Observed in `frontend/app/src/components/InvestigationGraph.tsx`: save status is not marked dirty when the graph changes. The UI can keep showing “Saved …” during the 2-second debounce window even though the current graph state is not yet persisted.
- Observed in `frontend/app/src/components/InvestigationGraph.tsx` plus `src/api/routers/graph.py:save_session_snapshot` plus `src/services/graph_sessions.py:save_workspace_snapshot`: autosave requests are not serialized and the backend does blind last-write-wins updates. The `requestId` guard only protects the toolbar label, not database write ordering. Older in-flight saves can still overwrite newer graph state if request completion order flips.

## Test Coverage Assessment

- Observed: backend coverage is decent for the server contract itself.
  - `tests/test_trace_compiler/test_session_persistence.py:test_create_session_persists_initial_workspace_snapshot` proves the create path includes a snapshot payload in the SQL call.
  - `tests/test_trace_compiler/test_session_endpoints.py:test_returns_full_workspace_when_snapshot_is_v1` proves backend restore returns a stored v1 workspace.
  - `tests/test_trace_compiler/test_session_endpoints.py:test_accepts_full_workspace_payload_and_persists_it` proves the snapshot endpoint accepts and stores a full workspace payload.
- Observed: this is still mostly unit/API-surface proof, not end-to-end behavioral proof.
  - create-time snapshot persistence is tested at the SQL argument level, not by creating a session and restoring it through the real routed API.
  - restore authority is not tested through the frontend at all.
  - autosave authority is not tested through the frontend at all.
- Observed: the frontend validation was `npm run build`. That proves type/compile integrity only. It does not prove network behavior, ordering, save-state correctness, or degraded-restore handling.
- Observed: no test covers the newly introduced race window around overlapping autosaves.
- Observed: no test covers the local-storage gate on restore affordance.
- Verdict on validation quality: backend contract coverage is meaningful, frontend authority coverage is weak.

## Regression Risks

- Inferred from `frontend/app/src/components/InvestigationGraph.tsx` and backend blind overwrite behavior: overlapping autosave requests can race and leave the server with an older snapshot than the latest visible UI state.
- Observed in `frontend/app/src/components/InvestigationGraph.tsx`: the toolbar can report “Saved” while the graph is dirty but still inside the debounce window.
- Observed in `frontend/app/src/components/SessionStarter.tsx`: restore discoverability still depends on local storage. Clearing browser storage removes the restore path even when the backend session exists.
- Observed in `frontend/app/src/App.tsx`: degraded `legacy_bootstrap` restores are silent. Investigators are not told they restored a reduced session rather than a full workspace snapshot.
- Observed in `frontend/app/src/components/InvestigationGraph.tsx`: manual JSON import remains able to overwrite the in-memory graph and then autosave that imported state back to the backend. This is explicit user action, not a silent override, but it does mean the backend authority is intentionally mutable from imported client state.

## Verdict

- Observed in code/runtime: the claimed Wave 02 direction is real. Initial snapshot persistence on create exists, restore now reads `GET /sessions/{id}`, and autosave now targets `POST /sessions/{id}/snapshot`.
- Observed in code/runtime: the stronger claim that Wave 02 fully made backend session state the real restore/autosave authority is only partially true.
  - True for workspace content authority once restore/autosave are invoked.
  - Not fully true for restore discovery, degraded-restore honesty, or autosave ordering safety.
- Observed in tests: the backend contract is meaningfully covered.
- Observed in tests/build: the frontend behavior is not meaningfully proven. The validation mostly proves “the code compiles” plus “the backend endpoints work in isolation.”
- Final hostile verdict: implementation is directionally correct but only partially verified.

## Required Fixes Before Wave 03

- Observed need: add frontend-level restore/autosave verification. Minimal acceptable proof is a real UI test harness or an end-to-end browser test that covers:
  - restore button click
  - `GET /sessions/{id}` request
  - graph import from `response.workspace`
  - autosave `POST /sessions/{id}/snapshot`
- Observed need: surface `restore_state` in the UI. `legacy_bootstrap` cannot remain silent.
- Observed need: mark the session dirty immediately on graph changes instead of waiting until the debounce timer fires.
- Observed need: prevent stale autosave overwrites. Options include serializing snapshot saves, aborting superseded requests, or adding a version/monotonic timestamp check server-side.
- Inferred need: decide whether backend session recovery must remain gated by local browser storage or whether session discovery/listing is required for truly server-authoritative recovery.

## Safe To Proceed To Wave 03? (Yes/No/Yes With Conditions)

- No.
- Reason:
  - Observed in code/runtime: the core Wave 02 path exists.
  - Observed in code/runtime/tests: the validation does not meaningfully prove the frontend authority claim.
  - Observed in code/runtime: there are still correctness-sensitive gaps around degraded restore honesty, dirty-state signaling, and autosave race ordering.
  - Inferred from evidence: moving on to unrelated Wave 03 work without closing those gaps would treat a partially verified session-authority change as settled when it is not.
