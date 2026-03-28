# Wave 02 Remediation

## Objective

Complete the unfinished Wave 02 session-authority cutover identified by the hostile verification pass. The goal of this run was to close the remaining blockers around restore discovery, `restore_state` handling, autosave stale-write protection, and weak proof so backend session state is actually authoritative in practice, not just in intent.

## Hostile Findings Reviewed

- Observed in code/runtime: restore discovery still depended on browser-local storage.
- Observed in code/runtime: `restore_state` existed in the backend contract but the frontend ignored it.
- Observed in code/runtime: autosave still allowed stale last-write-wins overwrites because requests were not ordered server-side.
- Observed in tests/build: frontend validation was weak and did not meaningfully prove the authority claim.

## Confirmed Findings

- Confirmed:
  - Observed in `frontend/app/src/components/SessionStarter.tsx` before this run: the restore affordance was gated by `loadSavedWorkspace()`, so backend sessions were undiscoverable if local storage was cleared.
  - Observed in `frontend/app/src/App.tsx` before this run: restore imported `response.workspace` but ignored `response.restore_state`.
  - Observed in `frontend/app/src/components/InvestigationGraph.tsx` plus `src/api/routers/graph.py` plus `src/services/graph_sessions.py` before this run: autosave used request-local UI guards only; older in-flight writes could still overwrite newer state in PostgreSQL.
  - Observed in the repo test surface before this run: there was still no meaningful proof that restore discovery was backend-driven or that stale snapshot writes were blocked.

## Rejected / Not Reproduced Findings

- None.
- Observed in code/runtime: every hostile blocker reproduced directly in the current repo before patching.

## Why This Was Delayed Wave 02 Work

- Observed in `WAVE_02_HOSTILE_VERIFICATION.md`: the prior Wave 02 implementation was only partially verified and explicitly blocked progression to true Wave 03.
- Observed in code/runtime: the remaining work was still part of the same session-authority boundary, not new feature work.
- Inferred from evidence: moving on to bridge freshness or empty-state semantics before closing these gaps would have left a partially trusted restore/autosave contract in place.

## Files Changed

- `src/trace_compiler/models.py`
- `src/services/graph_sessions.py`
- `src/api/routers/graph.py`
- `frontend/app/src/types/graph.ts`
- `frontend/app/src/api/client.ts`
- `frontend/app/src/App.tsx`
- `frontend/app/src/components/SessionStarter.tsx`
- `frontend/app/src/components/InvestigationGraph.tsx`
- `tests/test_trace_compiler/test_session_endpoints.py`
- `tests/test_trace_compiler/test_session_persistence.py`
- `tests/test_api/test_browser_surface.py`

## Summary of Code Changes

- Observed in `src/api/routers/graph.py` and `src/services/graph_sessions.py`:
  - added `GET /api/v1/graph/sessions/recent` so restore discovery no longer depends on browser-local session storage
  - added recent-session response models and backend listing support
- Observed in `frontend/app/src/App.tsx`, `frontend/app/src/api/client.ts`, and `frontend/app/src/components/SessionStarter.tsx`:
  - switched restore discovery to backend recent sessions, with local storage retained only as a hint for choosing among backend-owned sessions
  - removed the local-storage gate from the restore UI
- Observed in `frontend/app/src/App.tsx` and `frontend/app/src/components/InvestigationGraph.tsx`:
  - wired `restore_state` into investigator-visible behavior by surfacing a notice when the restored session is only `legacy_bootstrap`
- Observed in `src/trace_compiler/models.py`, `src/api/routers/graph.py`, `src/services/graph_sessions.py`, and `frontend/app/src/components/InvestigationGraph.tsx`:
  - added monotonic workspace `revision` tracking
  - changed snapshot save to compare-and-set on the previous revision at the database write boundary
  - return `409 Stale workspace snapshot revision` when a write loses the race
  - issue increasing client revisions on autosave so older in-flight saves cannot silently overwrite newer graph state
- Observed in `frontend/app/src/components/InvestigationGraph.tsx`:
  - added explicit dirty-state labeling so the toolbar no longer presents the current graph as fully saved during a changed-but-not-yet-persisted debounce window

## Tests Added or Updated

- Updated `tests/test_trace_compiler/test_session_endpoints.py`
  - recent-session discovery endpoint coverage
  - full-workspace save now asserts persisted revision and response revision
  - legacy node-state upgrade now asserts persisted revision
  - stale revision rejection coverage
  - database compare-and-set conflict coverage (`UPDATE 0` -> `409`)
- Updated `tests/test_trace_compiler/test_session_persistence.py`
  - initial create-session snapshot now asserts persisted revision `0`
- Updated `tests/test_api/test_browser_surface.py`
  - restore discovery now checks for backend recent-session usage instead of a SessionStarter local-storage gate
  - `restore_state` investigator notice is statically asserted
  - revision guardrails are statically asserted across frontend/backend source

## Validation Performed

- Ran:
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
- Ran:
  - `npm run build` in `frontend/app`

## Results

- Observed in test output: `86 passed, 0 failed`
- Observed in frontend build output: production build passed
- Observed in build output: existing large-chunk Vite warnings remain, but the remediation compiled and bundled successfully
- Observed in code/runtime:
  - initial snapshot persistence on create remained intact
  - restore still uses `GET /sessions/{id}`, but discovery is now backend-driven instead of local-storage-gated
  - autosave still uses `POST /sessions/{id}/snapshot`, but older writes can no longer silently win after a newer revision reaches the database first
  - `legacy_bootstrap` is no longer a silent contract path

## Remaining Risks

- Observed in code/runtime: there is still no dedicated frontend runtime harness for restore/autosave behavior; current frontend proof is stronger than before but still relies on build plus browser-surface assertions rather than a live interaction test.
- Observed in code/runtime: session-row creation still begins inside `TraceCompiler.create_session`; the persistence boundary is improved but not fully consolidated.
- Observed in code/runtime: bridge-hop freshness and empty-state honesty remain open and are now the real next correctness priorities.

## Conditions Required Before True Wave 03

- True Wave 03 can proceed only with the current session-authority guardrails kept intact:
  - keep `GET /sessions/recent` as the restore discovery source
  - keep `restore_state` surfaced to the investigator
  - keep revision-based snapshot protection and the `409` conflict path
  - keep the new regression tests green
- If true Wave 03 touches restore or autosave again, it must preserve equivalent authority coverage and rerun the same focused backend suite plus the frontend build.
