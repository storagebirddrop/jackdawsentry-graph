# Wave 01 Implementation

## Objective

Remove the current dishonest backend success paths and the proven `resolve-tx` runtime bug, then restore a trustworthy session-contract test signal around those exact behaviors.

## Why This Wave First

- It fixes the highest-value correctness/trust failures already proven by code and Phase 3 runtime evidence.
- It has the lowest unnecessary blast radius: no frontend contract changes, no new persistence layer, no snapshot schema rewrite.
- It clears the focused suite from `66 passed, 6 failed` to a green baseline that later waves can build on.

## Files Changed

- `src/trace_compiler/compiler.py`
- `src/api/routers/graph.py`
- `src/trace_compiler/models.py`
- `tests/test_trace_compiler/test_session_persistence.py`
- `tests/test_trace_compiler/test_session_endpoints.py`
- `tests/test_api/test_graph_app.py`

## Summary of Code Changes

- Added `SessionPersistenceError` in `src/trace_compiler/compiler.py`.
- Changed `TraceCompiler.create_session` so API-owned sessions fail loudly when:
  - the PostgreSQL pool is missing
  - the `graph_sessions` INSERT raises
- Changed `create_investigation_session` in `src/api/routers/graph.py` to translate `SessionPersistenceError` into `503 Session store unavailable`.
- Changed `save_session_snapshot` in `src/api/routers/graph.py` so snapshot write failures now return `503 Session store unavailable` instead of logging and returning success.
- Changed `TxResolveResponse.timestamp` in `src/trace_compiler/models.py` from `Optional[str]` to `Optional[datetime]` so valid DB-hit responses serialize cleanly.
- Replaced stale persistence and endpoint tests with Wave-01-specific expectations:
  - persistence failure raises instead of succeeding
  - snapshot save failure returns `503`
  - invalid session IDs return `400`
  - auth/OpenAPI assertions now match actual auth-disabled vs auth-enabled app construction
  - added a `resolve-tx` regression test proving datetime serialization

## Tests Added or Updated

- Updated `tests/test_trace_compiler/test_session_persistence.py`
  - replaced permissive PG-failure success test
  - added no-PG owned-session failure test
- Replaced `tests/test_trace_compiler/test_session_endpoints.py`
  - create-session success under auth-disabled runtime
  - create-session `503` on persistence failure
  - invalid UUID rejection for get/expand/bridge-status/save-snapshot
  - snapshot-save `503` on DB write failure
- Updated `tests/test_api/test_graph_app.py`
  - split OpenAPI docs assertions for auth-disabled and auth-enabled app construction
  - added `resolve-tx` datetime serialization regression

## Validation Performed

- Ran:
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`

## Results

- Result: `72 passed, 0 failed`
- Prior focused baseline was `66 passed, 6 failed`
- Net effect:
  - false-success create/snapshot paths now surface as `503`
  - `resolve-tx` DB-hit serialization no longer rejects datetime payloads
  - stale smoke/auth assumptions no longer keep the focused suite red

## Remaining Risks

- `get_investigation_session` is still a stub restore path returning fake empty graph arrays.
- Frontend restore remains local-storage authoritative.
- Mounted bridge polling is still absent from the active inspector path.
- Empty-state honesty from Phase 3 remains deferred.
- `tasks/memory.md`, `tasks/lessons.md`, and `tasks/todo.md` are still out of sync with the intended session-boundary end state.

## Follow-up Needed Before Wave 02

- Introduce a real backend session workspace contract:
  - `GraphSessionStore`
  - `WorkspaceSnapshotV1`
  - `restore_state`
- Replace stub restore with authoritative server workspace payloads.
- Preserve compatibility for legacy session rows while moving the frontend to server-owned state.
