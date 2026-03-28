# PHASE5_SECURITY_RELEASE_GATE

## SECTION 1 — EXECUTIVE VERDICT

`not ready`

`OBSERVED:` the current `jackdawsentry-graph` checkout is still a pre-Phase-4 session-contract snapshot. The code paths that Phase 3 proved unsafe are still present in `src/trace_compiler/compiler.py` `TraceCompiler.create_session`, `src/api/routers/graph.py` `get_investigation_session` / `save_session_snapshot`, `frontend/app/src/App.tsx` `handleRestoreWorkspace`, and `frontend/app/src/components/GraphInspectorPanel.tsx` `BridgeSection`; `frontend/app/src/components/BridgeHopDrawer.tsx` still exists as the only visible polling owner. The latest targeted session test run is still `66 passed, 6 failed`, and there is no fresh implementation diff, perf packet, rollout evidence, or updated decision docs proving the Phase 4 repair route landed. This slice is not safe to release.

## SECTION 2 — MOST DANGEROUS REMAINING RISKS

1. `OBSERVED:` investigator-facing session truth is still broken. `src/trace_compiler/compiler.py` `TraceCompiler.create_session` still documents and implements swallowed persistence failures, while `src/api/routers/graph.py` `get_investigation_session` still returns stubbed empty `nodes` and `edges`.
2. `OBSERVED:` the frontend still restores authoritative investigation state from browser-local storage in `frontend/app/src/App.tsx` `handleRestoreWorkspace`, not from the backend session snapshot contract.
3. `OBSERVED:` bridge-hop freshness is still detached from the mounted UI. `frontend/app/src/components/GraphInspectorPanel.tsx` `BridgeSection` renders status, but repo search still shows `frontend/app/src/components/BridgeHopDrawer.tsx` as the only visible polling owner.
4. `OBSERVED:` auth/test integrity is still weak. `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled` and 5 stale endpoint tests in `tests/test_trace_compiler/test_session_endpoints.py` still fail in the current targeted run, and `tests/conftest.py` still globally mutates test env.
5. `OBSERVED:` release confidence is unsupported by evidence. No fresh performance outputs, rollout notes, screenshots, or API samples prove that the Phase 4 spec was implemented or that the current slice survives hostile workloads.

## SECTION 3 — CORRECTNESS REMAINING RISKS

- `OBSERVED:` `src/trace_compiler/compiler.py` `TraceCompiler.create_session` still advertises that persistence failures are swallowed and the session is still returned to the caller. This leaves open the exact Phase 3 failure where investigators receive durable-looking `session_id` values that later do not restore.
- `OBSERVED:` `src/api/routers/graph.py` `get_investigation_session` still returns metadata plus `snapshot`, but hardcodes `nodes: []`, `edges: []`, and `branch_map: {}`. That is a silent semantic defect because the endpoint claims to restore a saved investigation while returning an empty graph shell.
- `OBSERVED:` `src/api/routers/graph.py` `save_session_snapshot` still writes only serialized `node_states` and logs `Failed to save snapshot...` on exception without converting that failure into a hard API error. That keeps the “save may have failed but the session looks valid” trust problem alive.
- `OBSERVED:` `frontend/app/src/App.tsx` `handleRestoreWorkspace` still loads from `loadSavedWorkspace()` and imports browser-local snapshot state into the store. Investigator continuity still depends on local browser state rather than server truth.
- `INFERRED from unchanged code + Phase 3 runtime evidence:` the `resolve-tx` timestamp typing fault in `src/api/routers/graph.py` `resolve_transaction` and `src/trace_compiler/models.py` is likely still live because the planned Phase 4 type change is absent from the current snapshot. No new test or code diff disproves that.
- `INFERRED from unchanged code + Phase 3 runtime evidence:` empty-state honesty is still at risk because `src/trace_compiler/compiler.py` `_build_empty_state` has not been refactored to distinguish indexed truth from directional emptiness. No current test proves that a completed ingest with one populated direction now renders honestly.
- `OBSERVED:` no current code path or attached screenshot proves that the mounted inspector refreshes bridge-hop state. That leaves bridge status truth stale under normal investigator interaction.

## SECTION 4 — SECURITY / ABUSE RISKS

- `OBSERVED:` ownership checks do exist in `src/api/routers/graph.py` `_get_owned_session_row`, but release confidence is still weak because the auth-mode contract is not stable in tests. The current targeted run still fails `tests/test_api/test_graph_app.py::test_graph_app_openapi_can_be_enabled`, and `tests/test_trace_compiler/test_session_endpoints.py` still assumes the wrong auth behavior for this repo state.
- `OBSERVED:` `src/api/config.py` `validate_graph_auth_disabled_safety` is a real fail-closed guardrail, but `tests/conftest.py` still globally mutates env for tests. That means the test suite still does not give a trustworthy release signal about production auth posture.
- `OBSERVED:` `src/trace_compiler/models.py` does enforce expansion caps through `ExpandOptions.max_results <= 100` and `page_size <= 50`. That is a real ceiling, but it is only a model-level guardrail. There is no fresh hostile-load evidence showing those limits are sufficient against dense graph hubs, repeated snapshots, or bridge-heavy expansions.
- `INFERRED:` snapshot/state abuse remains plausible because the current frontend still treats local workspace state as authoritative and the backend still does not expose a hardened full-workspace restore/save contract. That creates room for silent client/server divergence rather than a clean owner-bound server state model.
- `CLAIMED ONLY in docs/todo:` shipped status around bridge/status/session workflow is not trustworthy enough to count as abuse-resistance evidence. `tasks/todo.md` still contains completed workflow claims, but no implementation diff or fresh proof packet matches them.

## SECTION 5 — PERFORMANCE / RESILIENCE RISKS

- `OBSERVED:` there are no fresh perf artifacts in the repo snapshot beyond `pytest.ini`; no benchmark output, soak report, or release-candidate workload evidence was attached. Performance confidence is currently unsupported.
- `OBSERVED:` the current slice still lacks the Phase 4 server-snapshot/autosave implementation, so there is no evidence that snapshot write amplification was solved or even measured.
- `OBSERVED:` `frontend/app/src/components/BridgeHopDrawer.tsx` still exists while mounted polling is absent from `frontend/app/src/components/GraphInspectorPanel.tsx`. Even if bridge polling were manually wired later, there is no current evidence about timer cleanup, repeated selection behavior, or request fan-out under adversarial node-clicking.
- `OBSERVED:` targeted tests still fail in the current release candidate: `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q` produced `66 passed, 6 failed`. A release gate with a knowingly red targeted slice is not credible.
- `INFERRED from Phase 3 runtime evidence:` the previously observed cold-start compose/API readiness weakness remains unresolved because no deployment/config update or fresh rollout packet was attached. Even if treated as adjacent rather than core slice logic, it still erodes operational confidence.

## SECTION 6 — DOCUMENTATION / DECISION DRIFT RISKS

- `OBSERVED:` `tasks/memory.md` still does not contain the Phase 4 decisions that server-backed workspace snapshots are authoritative, that session persistence moved to a dedicated store/service boundary, or that mounted inspector code owns bridge-hop polling.
- `OBSERVED:` `tasks/lessons.md` still does not record the concrete lessons from Phase 3 and Phase 4: do not bless swallowed persistence errors, do not let global env overrides hide safety validators, and do not treat unmounted UI paths as shipped behavior.
- `OBSERVED:` `tasks/todo.md` still reads as if key workflow passes are complete, but the current code snapshot and test baseline do not support those completion claims for the session slice.
- `OBSERVED:` the phase review/spec docs themselves are untracked in `git status --short`, and there is no tracked implementation diff matching them. That is a release-integrity failure: the repo cannot show a clean chain from review to patch to gate.
- `INFERRED:` if the team ships without closing this doc drift, the next engineer will inherit the same false story: docs will claim a repaired session contract while code still behaves like the stub-era implementation.

## SECTION 7 — REQUIRED FIXES BEFORE RELEASE

- `OBSERVED:` implement the Phase 4 session-contract repair route in code. At minimum, the current behaviors in `src/trace_compiler/compiler.py` `TraceCompiler.create_session`, `src/api/routers/graph.py` `get_investigation_session`, and `src/api/routers/graph.py` `save_session_snapshot` must be replaced by a truthful server-backed session contract.
- `OBSERVED:` remove browser-local authoritative restore from `frontend/app/src/App.tsx` and replace it with server-authoritative restore semantics.
- `OBSERVED:` move bridge-hop polling into the mounted inspector path and eliminate the current dead-owner state where `BridgeHopDrawer.tsx` is the only polling implementation.
- `OBSERVED:` resolve the current targeted test failures and replace stale endpoint/auth tests with tests that reflect real auth-enabled and auth-disabled behavior.
- `INFERRED from unchanged code + Phase 3 evidence:` patch the `resolve-tx` timestamp serialization fault and the empty-state honesty fault before release. No fresh code or tests prove those runtime defects are gone.
- `OBSERVED:` update `tasks/memory.md`, `tasks/lessons.md`, and `tasks/todo.md` so the repo’s durable decisions and shipped-state claims match the actual implementation.
- `OBSERVED:` provide fresh release evidence: implementation diff, clean targeted test pass, relevant payload samples, perf outputs, and rollout/deployment notes.

## SECTION 8 — SHOULD-FIX SOON AFTER

- `OBSERVED:` clean up remaining auth/env test drift beyond the must-fix endpoint failures so the suite is a trustworthy release signal rather than a partly historical artifact.
- `INFERRED:` add hostile workload/perf coverage for dense branches, repeated expands, and repeated snapshot saves once the Phase 4 implementation exists.
- `INFERRED:` add explicit operational health evidence around cold-start readiness and dependency startup sequencing if this slice is going into a fresh deploy path.
- `OBSERVED:` address the current warning debt surfaced by the targeted pytest run, especially the repeated Pydantic deprecation warnings, so real release regressions are easier to spot.

## SECTION 9 — RELEASE CHECKLIST

- [ ] `POST /api/v1/graph/sessions` fails cleanly when persistence fails and does not return ghost `session_id` values.
- [ ] `GET /api/v1/graph/sessions/{id}` restores authoritative server workspace state, not fake empty graph arrays.
- [ ] Browser refresh restores from server state, not browser-local full graph storage.
- [ ] `POST /api/v1/graph/sessions/{id}/snapshot` fails loudly on write error.
- [ ] Mounted bridge inspector polling exists, is visible, and reaches terminal hop states correctly.
- [ ] `resolve-tx` and post-ingest empty-state honesty are proven fixed by tests or payload evidence.
- [ ] Auth-enabled and auth-disabled behaviors are tested separately; no global env masking remains in the release signal.
- [ ] Targeted session-contract tests are green.
- [ ] Fresh perf/resilience evidence exists for dense expand and repeated save behavior.
- [ ] `tasks/memory.md`, `tasks/lessons.md`, and `tasks/todo.md` match the shipped implementation.
- [ ] The candidate release is backed by a clean, reviewable implementation diff and archived evidence set.

## SECTION 10 — FINAL PLAIN-ENGLISH VERDICT

This slice cannot ship from the current checkout. The dangerous problems are not cosmetic. The backend still looks capable of issuing session IDs that do not represent durable truth, the restore endpoint still does not restore an actual graph, the frontend still trusts browser-local graph state, bridge-hop freshness still lives in dead UI code, and the targeted test baseline is still red in the exact places that expose contract drift. There is also no fresh performance or rollout packet proving abuse resistance or operational safety. The right verdict is `not ready`.

## SECTION 11 — OUTPUT FILE TO SAVE

- save this document as `PHASE5_SECURITY_RELEASE_GATE.md`

## SECTION 12 — ARCHIVE / HANDOFF

- `PHASE1_REALITY_MAP.md`
- `PHASE2_HOSTILE_REVIEW.md`
- `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`
- `PHASE4_REFACTOR_PATCH_SPEC.md`
- `PHASE5_SECURITY_RELEASE_GATE.md`
- latest relevant code snapshot
- latest test outputs
- latest perf outputs
- latest logs/screenshots if relevant
- updated `tasks/memory.md`
- updated `tasks/lessons.md`
- updated `todo.md` if changed

## SECTION 13 — PHASE EXIT CRITERIA

- ship verdict is explicit
- release blockers are explicit
- residual correctness, security, and performance risks are ranked
- documentation drift is explicitly assessed
- final archive set is explicitly listed
