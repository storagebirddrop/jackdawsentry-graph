# Wave 04 Implementation

## Objective

Patch empty-state honesty so address expansions no longer imply “no known activity” when the current graph dataset already contains indexed activity for that address in the requested direction or the opposite direction.

## Why This Wave Next

- Observed in code: the Wave 03 bridge freshness changes are present in the active inspector path (`frontend/app/src/components/InvestigationGraph.tsx`, `frontend/app/src/components/GraphInspectorPanel.tsx`, `frontend/app/src/store/graphStore.ts`).
- Observed in code: the Wave 02 remediation guardrails are still present:
  - backend restore discovery (`src/api/routers/graph.py`, `frontend/app/src/App.tsx`, `frontend/app/src/api/client.ts`)
  - surfaced `restore_state` (`frontend/app/src/App.tsx`)
  - snapshot revision protection (`src/api/routers/graph.py`, `src/services/graph_sessions.py`)
- Observed in code: `_build_empty_state` in `src/trace_compiler/compiler.py` still relied on live lookup hints and did not inspect indexed event-store directionality before choosing its message.
- Inferred from evidence: empty-state honesty remained the highest-value unresolved investigator-truth issue with a lower blast radius than bundling docs, perf, and release-packet work into the same run.

## Wave 04 Readiness Summary

- Observed in code: Wave 3 is actually reflected in the repo.
- Observed in code: Wave 2 remediation remains intact.
- Observed in executed tests/build/lint/typecheck: the focused regression suite passed after this wave, and the frontend production build still succeeds.
- Inferred from evidence: no reopened Wave 2 or Wave 3 blocker was found that would make true Wave 4 premature.
- Conclusion: the repo was clear to proceed to true Wave 4 work.

## Execution Classification

True Wave 04 work

## Files Changed

- `src/trace_compiler/compiler.py`
- `tests/test_trace_compiler/test_compiler_stub.py`
- `WAVE_04_IMPLEMENTATION.md`
- `WAVE_04_PROOF.md`
- `NEXT_WAVE_HANDOFF.md`
- `MASTER_EXECUTION_PLAN_UPDATED.md`

## Summary of Code Changes

- Observed in code: added `_opposite_operation_phrase()` in `src/trace_compiler/compiler.py` so empty-state messaging can describe the opposite traversal direction cleanly.
- Observed in code: added `_get_indexed_activity_presence()` in `src/trace_compiler/compiler.py`.
  - For account-style chains, it checks `raw_transactions` and `raw_token_transfers` for indexed outbound and inbound presence.
  - For Bitcoin, it checks `raw_utxo_inputs` and `raw_utxo_outputs` for indexed outbound and inbound presence.
- Observed in code: updated `_build_empty_state()` in `src/trace_compiler/compiler.py` to prefer indexed-truth messaging before falling back to the older live-lookup-only wording.
  - When indexed activity exists in the requested direction, the message now says the request produced no new graph results and explains that activity may already be visible, filtered out, or not promotable into new nodes/edges.
  - When indexed activity exists only in the opposite direction, the message now says so explicitly.
  - When no indexed evidence exists, the older live-lookup and on-chain-observation logic still applies.
- Observed in code: added targeted regression tests in `tests/test_trace_compiler/test_compiler_stub.py` for:
  - requested-direction indexed activity with no new graph results
  - opposite-direction indexed activity
  - Bitcoin directional presence via UTXO tables

## Tests Added or Updated

- Updated `tests/test_trace_compiler/test_compiler_stub.py`
  - `test_build_empty_state_reports_indexed_requested_direction_without_new_results`
  - `test_build_empty_state_reports_indexed_other_direction_activity`
  - `test_build_empty_state_uses_bitcoin_event_store_directional_presence`

## Validation Performed

- Observed in executed tests/build/lint/typecheck:
  - `.venv/bin/pytest tests/test_trace_compiler/test_compiler_stub.py -q`
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
  - `npm run build` in `frontend/app`

## Results

- Observed in executed tests/build/lint/typecheck: `tests/test_trace_compiler/test_compiler_stub.py -q` passed (`22 passed`).
- Observed in executed tests/build/lint/typecheck: the focused regression suite passed (`90 passed, 0 failed`).
- Observed in executed tests/build/lint/typecheck: the frontend production build passed.
- Observed in executed tests/build/lint/typecheck: existing Pydantic deprecation warnings and Vite large-chunk warnings remain, but they did not block this wave.
- Observed in code: the backend now has an explicit truth path for “indexed activity exists, but not as new results for this request.”
- Inferred from evidence: because `frontend/app/src/components/InvestigationGraph.tsx` still renders `response.empty_state?.message`, investigators will now see the more honest backend wording when this path is hit.

## Remaining Risks

- Observed in code: there is still no executed browser/runtime proof for the exact empty-state notice text in the running UI.
- Observed in code: `BridgeHopDrawer.tsx` remains dead code and can still confuse ownership.
- Observed in code: docs are still behind implementation reality (`tasks/memory.md`, `tasks/lessons.md`, `todo.md`, `README.md`, `SECURITY.md`).
- Observed in code: session creation persistence is still split between `TraceCompiler.create_session` and `GraphSessionStore`.
- Inferred from evidence: release confidence still depends on the final docs/perf/release-packet wave rather than this correctness wave alone.

## Follow-up Needed Before Wave 05

- Align docs with shipped behavior:
  - `tasks/memory.md`
  - `tasks/lessons.md`
  - `todo.md`
  - `README.md`
  - `SECURITY.md`
- Gather perf and rollout evidence for the repaired session/bridge/empty-state flows.
- Refresh the final release-gate packet against the current implementation state.
