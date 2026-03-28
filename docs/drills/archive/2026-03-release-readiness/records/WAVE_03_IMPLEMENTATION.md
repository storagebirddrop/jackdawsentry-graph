# Wave 03 Implementation

## Objective

Move bridge-hop freshness into the mounted investigator path so a selected pending bridge hop updates in the active inspector and canvas instead of relying on the dead `BridgeHopDrawer` polling path.

## Why This Wave Next

- Observed in code/runtime: Wave 02 remediation is present in the repo and its session-authority guardrails remain intact.
- Observed in code/runtime: the active inspector still rendered bridge-hop state without polling, while the only poller lived in `frontend/app/src/components/BridgeHopDrawer.tsx`, which is not mounted anywhere in the active UI.
- Inferred from evidence: bridge-hop freshness was the highest remaining investigator-truth issue with a smaller blast radius than combining it with the backend empty-state honesty fix in the same run.
- Observed in code/runtime: prior Wave 02 blockers checked clear before this run:
  - backend recent-session discovery exists
  - `restore_state` is surfaced
  - snapshot revision guardrails remain in place
  - focused Wave 02 regression suite remains green

## Wave 03 Readiness Summary

- Observed in `WAVE_02_REMEDIATION.md`: the prior run declared the repo safe to proceed to true Wave 03.
- Observed in code/runtime: that claim is reflected in the repo:
  - `GET /api/v1/graph/sessions/recent` exists and is used for restore discovery
  - `restore_state` notices are surfaced in the frontend
  - snapshot saves use revision conflict protection
- Observed in code/runtime: no reopened Wave 02 blocker was found during this run.
- Conclusion: the repo was verified clear for true Wave 03.

## Files Changed

- `frontend/app/src/hooks/useBridgeHopPoller.ts`
- `frontend/app/src/components/InvestigationGraph.tsx`
- `frontend/app/src/components/GraphInspectorPanel.tsx`
- `frontend/app/src/components/graphVisuals.tsx`
- `frontend/app/src/store/graphStore.ts`
- `frontend/app/src/types/graph.ts`
- `tests/test_api/test_browser_surface.py`

## Summary of Code Changes

- Added `useBridgeHopPoller` in `frontend/app/src/hooks/useBridgeHopPoller.ts`.
  - Polls `GET /api/v1/graph/sessions/{session_id}/hops/{hop_id}/status` every 30 seconds while the actively selected bridge hop is still `pending`.
  - Stops polling on terminal states.
  - Surfaces transient poll failures locally in the active inspector instead of silently dropping them.
- Updated `frontend/app/src/store/graphStore.ts`.
  - Added `updateBridgeHopStatus(nodeId, status)` to patch the canonical node map and React Flow nodes in place.
  - Propagates updated status, destination fields, and activity-summary destination metadata to the visible node.
  - Avoids churn when the polled status payload does not materially change the node.
- Updated `frontend/app/src/components/InvestigationGraph.tsx`.
  - Wired the mounted selection path to the new poller.
  - Passed live bridge refresh state into `GraphInspectorPanel`.
  - Updated the file header comment to match current behavior instead of the stale drawer-based description.
- Updated `frontend/app/src/components/GraphInspectorPanel.tsx`.
  - Added investigator-visible bridge refresh metadata in the active inspector:
    - polling state
    - last checked timestamp
    - transient polling error banner
- Updated `frontend/app/src/components/graphVisuals.tsx`.
  - Treated `expired` bridge status as terminal/red in the visual tone helper.
- Updated `frontend/app/src/types/graph.ts`.
  - Extended bridge-hop frontend types to include `expired` and `updated_at`.
- Updated `tests/test_api/test_browser_surface.py`.
  - Added a regression that asserts the mounted inspector path now owns bridge polling through `useBridgeHopPoller` plus store-based node patching.

## Tests Added or Updated

- Updated `tests/test_api/test_browser_surface.py`
  - `test_active_bridge_inspector_uses_mounted_bridge_hop_poller`
  - This verifies, at source-surface level, that:
    - `InvestigationGraph` uses `useBridgeHopPoller`
    - `GraphInspectorPanel` receives `bridgeStatusRefresh`
    - the graph store exposes `updateBridgeHopStatus`
    - the mounted path is the active polling owner

## Validation Performed

- Ran:
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
- Ran:
  - `npm run build` in `frontend/app`

## Results

- Observed in test output: `87 passed, 0 failed`
- Observed in build output: frontend production build passed
- Observed in build output: existing Vite large-chunk warnings remain, but the mounted bridge freshness path compiles and bundles successfully
- Observed in code/runtime:
  - bridge-hop status polling now runs in the active inspector path instead of the dead drawer path
  - a selected pending bridge hop can refresh in place on the canvas and in the inspector
  - terminal bridge states stop polling and surface the latest known status to the investigator

## Remaining Risks

- Observed in code/runtime: `BridgeHopDrawer.tsx` still exists as dead code and can still mislead future implementation work if left undocumented or unremoved.
- Observed in code/runtime: there is still no dedicated live frontend interaction harness for bridge polling; current proof is build plus browser-surface assertions rather than a runtime UI test.
- Observed in code/runtime: empty-state honesty remains unpatched in `src/trace_compiler/compiler.py`.
- Observed in code/runtime: docs and release evidence still lag the actual implementation state.
- Observed in code/runtime: session creation persistence is still split between `TraceCompiler.create_session` and `GraphSessionStore`.

## Follow-up Needed Before Wave 04

- Patch empty-state honesty so completed ingest plus indexed directional activity cannot present as “no known activity”.
- Add regression coverage around the empty-state reason/message/count path.
- After that correctness wave, update docs and gather perf/release evidence before the final release gate refresh.
