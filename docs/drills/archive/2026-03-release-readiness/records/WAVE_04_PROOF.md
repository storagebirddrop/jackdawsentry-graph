# Wave 04 Proof

## Claims Under Verification

- Wave 3 is actually present in the repo and Wave 2 remediation remains intact.
- This run is true Wave 04 work, not delayed Wave 3 follow-up.
- Empty-state handling now distinguishes indexed requested-direction activity from indexed opposite-direction activity.
- Bitcoin empty-state directionality now consults UTXO tables rather than only live lookup hints.
- The new empty-state logic is covered by executed regression tests.
- Prior-wave regressions were rechecked after this wave.

## Evidence Matrix

| Claim | Evidence Label | Exact Evidence | File Path / Test / Command / Repro Source | Strength Of Proof | Remaining Uncertainty |
|---|---|---|---|---|---|
| Wave 3 mounted bridge polling is present in the active UI path | Observed in code | `InvestigationGraph` imports `useBridgeHopPoller`; `GraphInspectorPanel` accepts and renders `bridgeStatusRefresh`; `graphStore` exposes `updateBridgeHopStatus` | `frontend/app/src/components/InvestigationGraph.tsx`, `frontend/app/src/components/GraphInspectorPanel.tsx`, `frontend/app/src/store/graphStore.ts` | High | No live browser interaction was executed in this run |
| Wave 2 remediation remains intact | Observed in code | backend recent-session discovery, `legacy_bootstrap` handling, and snapshot revision conflict protection remain in place | `src/api/routers/graph.py`, `src/services/graph_sessions.py`, `frontend/app/src/App.tsx`, `frontend/app/src/api/client.ts` | High | No separate runtime repro executed in this run |
| The repo was clear to proceed to true Wave 4 | Inferred from evidence | Prior-wave code paths were still present, and the focused regression suite plus build passed after this run | Code anchors above plus the test/build commands below | Medium | Readiness is inferred from code + regression evidence, not a pre-edit runtime repro |
| `_build_empty_state` now detects indexed requested-direction activity with no new graph results | Observed in code | New reason `indexed_activity_already_accounted_for`; new message branch before live-lookup fallback | `src/trace_compiler/compiler.py` | High | No separate runtime repro of a real API response was executed |
| `_build_empty_state` now detects indexed opposite-direction activity | Observed in code | New reason `indexed_activity_in_other_direction`; explicit opposite-direction wording | `src/trace_compiler/compiler.py` | High | No separate runtime repro of a real API response was executed |
| Empty-state directionality now queries indexed event-store presence for account-style chains | Observed in code | `_get_indexed_activity_presence()` checks `raw_transactions` and `raw_token_transfers` inbound/outbound presence | `src/trace_compiler/compiler.py` | High | Presence is based on event-store existence, not full transaction cardinality |
| Bitcoin empty-state directionality now queries indexed UTXO presence | Observed in code | `_get_indexed_activity_presence()` uses `raw_utxo_inputs` and `raw_utxo_outputs` when `chain == "bitcoin"` | `src/trace_compiler/compiler.py` | High | No real Bitcoin DB runtime repro was executed outside tests |
| The new empty-state branches are covered by executed tests | Observed in executed tests/build/lint/typecheck | New tests passed for requested-direction activity, opposite-direction activity, and Bitcoin UTXO presence | `tests/test_trace_compiler/test_compiler_stub.py`; command: `.venv/bin/pytest tests/test_trace_compiler/test_compiler_stub.py -q` | High | Tests are unit/compiler-level, not browser/runtime |
| Prior-wave regressions did not reopen in the focused suite | Observed in executed tests/build/lint/typecheck | Focused regression suite passed after the Wave 4 patch | Command: `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q` | High | This does not prove every browser interaction at runtime |
| Frontend integrity remained intact after the backend-only Wave 4 change | Observed in executed tests/build/lint/typecheck | Frontend production build passed | Command: `npm run build` in `frontend/app` | Medium | Build success does not prove runtime UI behavior |
| Investigators will now see the more honest empty-state wording in the UI | Inferred from evidence | `InvestigationGraph` still renders `response.empty_state?.message`, and the backend now generates more honest messages | `frontend/app/src/components/InvestigationGraph.tsx`, `src/trace_compiler/compiler.py` | Medium | No browser/runtime repro was executed for the rendered notice text |

## Regressions Checked

- Observed in executed tests/build/lint/typecheck: targeted compiler stub tests passed (`22 passed`).
- Observed in executed tests/build/lint/typecheck: focused regression suite passed (`90 passed, 0 failed`).
- Observed in executed tests/build/lint/typecheck: frontend production build passed.
- Observed in code: no change in this wave touched session restore/autosave code paths or the mounted bridge poller path.

## Prior-Wave Status Check

- Wave 1 status:
  - Observed in code: truthful session-create/session-save failure handling remains in place.
  - Observed in executed tests/build/lint/typecheck: focused suite still passes session contract tests.
- Wave 2 status:
  - Observed in code: backend workspace restore/save contract remains present.
- Wave 2 remediation status:
  - Observed in code: backend recent-session discovery, `restore_state`, and snapshot revision conflict protection remain present.
- Wave 3 status:
  - Observed in code: mounted bridge polling remains wired through the active inspector path.
  - Observed in executed tests/build/lint/typecheck: browser-surface checks and frontend build still pass.

## What Is Proven

- Observed in code: the empty-state builder now has explicit indexed-direction awareness.
- Observed in code: Bitcoin uses a UTXO-specific indexed-presence path.
- Observed in executed tests/build/lint/typecheck: the new empty-state branches are covered by passing tests.
- Observed in executed tests/build/lint/typecheck: prior-wave focused regressions remain green.
- Observed in executed tests/build/lint/typecheck: the frontend still builds after the Wave 4 change.

## What Is Only Inferred

- Inferred from evidence: the investigator-facing empty-state notice will now be more honest in the running UI because the frontend still renders the backend message field.
- Inferred from evidence: the repo is clear to proceed to Wave 05 because no reopened Wave 2 or Wave 3 blocker was found and the focused regressions stayed green.

## What Is Still Unverified

- Claimed in docs/history but not yet verified: any runtime/browser screenshot or manual repro of the new empty-state wording in the live UI.
- Claimed in docs/history but not yet verified: release-readiness beyond the focused regression suite and frontend build.
- Observed in code: docs remain stale relative to the current implementation.

## Safe To Proceed To Wave 05? (Yes / No / Yes With Conditions)

Yes With Conditions

Conditions:
- keep the focused regression suite green
- do not weaken the Wave 02 revision guardrails or the Wave 03 mounted bridge polling path during the docs/release-hardening wave
- treat runtime UI proof for the empty-state notice as still unverified until a concrete browser repro or screenshot exists
