# Wave 02 Implementation

## Objective

Complete the end-to-end session authority path so the investigation graph no longer depends on browser-local graph state as the source of truth. This wave closes the create-session snapshot gap, switches frontend restore to the backend workspace contract, and replaces local autosave with server-backed snapshot persistence plus visible save state.

## Why This Wave Next

- Observed in code/runtime: Wave 01 was fully present in the repo.
- Observed in code/runtime: the repo had already advanced beyond the old Wave 01 handoff. The backend restore/save contract that earlier planning treated as the next wave was already implemented.
- Inferred from evidence: the highest-value remaining correctness issue was no longer backend snapshot shape drift. It was the split-brain session authority path where the backend had a real workspace contract but the frontend still restored and autosaved from `localStorage`.
- Observed in code/runtime: this wave had the strongest dependency value for later work because bridge polling, empty-state honesty, and release hardening all depend on the graph session boundary being honest first.

## Wave 01 Verification Summary

- Observed in code/runtime: `TraceCompiler.create_session` still raises `SessionPersistenceError` for owned sessions when PostgreSQL persistence is unavailable.
- Observed in code/runtime: `create_investigation_session` still translates that failure into `503 Session store unavailable`.
- Observed in code/runtime: `save_session_snapshot` still returns `503 Session store unavailable` on snapshot write failure.
- Observed in code/runtime: `TxResolveResponse.timestamp` is still datetime-typed and the focused session suite covering that regression remained green.
- Observed in code/runtime: the stale Wave 01 endpoint/auth tests were still replaced and passing.
- Observed in code/runtime: the repo also already contained the Wave 02 backend groundwork from the prior handoff:
  - `GraphSessionStore`
  - `WorkspaceSnapshotV1`
  - normalized `GET /sessions/{id}`
  - upgraded `POST /sessions/{id}/snapshot`

Conclusion: Wave 01 was fully verified, but the old handoff assumptions were partially stale because the repo already contained the originally planned backend workspace-contract wave.

## Files Changed

- `frontend/app/src/App.tsx`
- `frontend/app/src/api/client.ts`
- `frontend/app/src/components/InvestigationGraph.tsx`
- `frontend/app/src/types/graph.ts`
- `frontend/app/src/workspacePersistence.ts`
- `src/trace_compiler/compiler.py`
- `tests/test_trace_compiler/test_session_persistence.py`

## Summary of Code Changes

- `frontend/app/src/App.tsx`
  - replaced restore-from-local-snapshot behavior with restore-from-backend-session behavior
  - backend restore now loads `GET /api/v1/graph/sessions/{id}` and imports `response.workspace`
  - local storage now only keeps a session reference, not the authoritative graph payload
- `frontend/app/src/api/client.ts`
  - added `getSession(sessionId)`
  - added `saveSessionSnapshot(sessionId, snapshot)`
- `frontend/app/src/types/graph.ts`
  - added frontend types for:
    - `WorkspaceSnapshotV1`
    - `InvestigationSessionResponse`
    - `SessionSnapshotResponse`
    - related workspace metadata types
- `frontend/app/src/workspacePersistence.ts`
  - narrowed persisted browser data to `sessionId` + `savedAt`
  - kept backward-compatible parsing for old local-storage entries that still contain `snapshot`
- `frontend/app/src/components/InvestigationGraph.tsx`
  - replaced local autosave with debounced server snapshot save
  - added explicit `saving` / `saved` / `save_failed` session status in the toolbar
  - kept export/import tooling intact for manual snapshot exchange
- `src/trace_compiler/compiler.py`
  - create-session now persists an initial root-only `WorkspaceSnapshotV1` into `graph_sessions.snapshot`
  - `snapshot_saved_at` is written at create time so fresh sessions are immediately restorable through the backend contract

## Tests Added or Updated

- Updated `tests/test_trace_compiler/test_session_persistence.py`
  - added `test_create_session_persists_initial_workspace_snapshot`
  - verifies the initial create-session INSERT now includes:
    - `snapshot`
    - `snapshot_saved_at`
    - a root-only workspace payload with the created session ID and root node

## Validation Performed

- Ran backend focused suite:
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
- Ran frontend build:
  - `npm run build` in `frontend/app`

## Results

- Backend validation result: `78 passed, 0 failed`
- Frontend validation result: build passed
- Observed in code/runtime:
  - sessions now persist an initial backend workspace snapshot at creation time
  - restore now goes through the backend session workspace payload instead of browser-local graph JSON
  - autosave now targets the backend snapshot endpoint
  - the UI exposes server-backed save state instead of silently implying persistence
- Observed in code/runtime:
  - Vite build still reports pre-existing large-chunk warnings after minification
  - no frontend test harness exists in this repo, so build validation was the meaningful frontend check available in this wave

## Remaining Risks

- Observed in code/runtime: `TraceCompiler.create_session` still owns the session-row INSERT path; persistence has not yet been fully extracted behind the store helper.
- Observed in code/runtime: mounted bridge-hop polling is still absent from the active inspector path.
- Inferred from evidence: empty-state honesty from Phase 3 remains wrong until `_build_empty_state` is patched.
- Observed in code/runtime: docs and release evidence still lag the actual implementation state.
- Observed in code/runtime: frontend build passes, but the repo still lacks a dedicated frontend test harness for restore/autosave behavior.

## Follow-up Needed Before Wave 03

- Fix investigator-facing freshness and honesty issues now that session authority is real:
  - move bridge-hop polling into the mounted inspector path
  - patch empty-state messaging/counts so completed ingest plus indexed inbound activity cannot read like “no known activity”
  - update `tasks/memory.md`, `tasks/lessons.md`, `todo.md`, README, and SECURITY to match shipped session behavior
  - gather perf and release evidence once the correctness fixes above are in
