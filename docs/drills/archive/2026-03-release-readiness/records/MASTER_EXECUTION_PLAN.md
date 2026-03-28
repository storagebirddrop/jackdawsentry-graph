# Master Execution Plan

## Scope

- Target subsystem: Session Contract only.
- In scope:
  - truthful session creation and snapshot persistence behavior
  - backend restore/save session workspace contract
  - `resolve-tx` response correctness
  - frontend/server session authority cutover
  - mounted bridge polling after the restore/save cutover is stable
  - session-boundary docs and release evidence
- Out of scope for the next wave:
  - broad tracing-model rewrites
  - compose cold-start hardening
  - unrelated chain semantics work

## Inputs Reviewed

- `PHASE1_REALITY_MAP.md`
- `PHASE2_HOSTILE_REVIEW.md`
- `PHASE3_DEBUGGING_ROOT_CAUSE_REPORT.md`
- `PHASE4_REFACTOR_PATCH_SPEC.md`
- `PHASE5_SECURITY_RELEASE_GATE.md`
- `tasks/memory.md`
- `tasks/lessons.md`
- `tasks/todo.md`
- Current code in:
  - `src/trace_compiler/compiler.py`
  - `src/api/routers/graph.py`
  - `src/trace_compiler/models.py`
  - `src/services/graph_sessions.py`
  - `frontend/app/src/App.tsx`
  - `frontend/app/src/workspacePersistence.ts`
  - `frontend/app/src/store/graphStore.ts`
  - `frontend/app/src/components/GraphInspectorPanel.tsx`
  - `frontend/app/src/components/BridgeHopDrawer.tsx`
- Current tests in:
  - `tests/test_trace_compiler/test_session_persistence.py`
  - `tests/test_trace_compiler/test_session_endpoints.py`
  - `tests/test_trace_compiler/test_session_security.py`
  - `tests/test_api/test_graph_app.py`
  - targeted session-contract pytest baseline

## Evidence Hierarchy Used

1. Observed in code/runtime/tests
2. Inferred from unchanged code plus earlier proven runtime evidence
3. Claimed in phase docs or `todo.md` but not yet verified

## Consolidated Findings

- Observed: Wave 01 is real in the repo. Session creation and snapshot writes now fail honestly with `503` on persistence failure, and `resolve-tx` no longer rejects valid datetime payloads.
- Observed: Wave 02 is now real in the repo. `GET /api/v1/graph/sessions/{session_id}` returns a normalized workspace payload plus `restore_state`, and `POST /api/v1/graph/sessions/{session_id}/snapshot` accepts full workspace snapshots while still upgrading legacy `node_states`.
- Observed: `src/services/graph_sessions.py` now exists as a narrow backend session workspace helper, but session creation still persists inside `TraceCompiler.create_session`.
- Observed: frontend restore remains local-storage authoritative. The active app still does not restore from the backend session workspace contract.
- Observed: mounted bridge polling is still absent from the active inspector path; `BridgeHopDrawer` remains the only visible polling owner.
- Observed: empty-state honesty from Phase 3 remains unpatched.
- Observed: project docs and release packet inputs still lag the actual implementation state.

## Resolved Contradictions

- Earlier phase documents described the intended repair route before it existed. Current code now partially matches that route, so the plan has been updated to reflect the implemented backend contract rather than the pre-Wave-02 state.
- The original Wave 03 bundled frontend authority cutover and mounted bridge polling. That is now too broad for the remaining highest-risk work, so the next wave is narrowed to end-to-end session authority first.

## Deferred / Unverified Claims

- Claimed: create-session persistence should already be owned by a dedicated session store. Not yet verified in code; `TraceCompiler.create_session` still owns the write path.
- Claimed: the frontend should already restore from server-backed workspace snapshots. Not yet implemented.
- Claimed: mounted bridge polling hook in the active inspector path. Not yet implemented.
- Inferred: empty-state honesty remains wrong. Phase 3 evidence is still strong, but this remains unpatched in the current code.

## Execution Waves

### Wave 1

- Status: complete and verified.
- Objective: remove dishonest backend success paths and the proven `resolve-tx` runtime bug, while restoring a trustworthy test signal.
- Outcome:
  - create-session persistence failures now return `503`
  - snapshot-save failures now return `503`
  - `resolve-tx` datetime serialization is fixed
  - stale session/auth tests were replaced
- Validation:
  - focused suite moved from `66 passed, 6 failed` to `72 passed, 0 failed`

### Wave 2

- Status: complete and verified.
- Objective: add a truthful backend restore/save workspace contract without pulling frontend cutover into the same change set.
- Outcome:
  - added `GraphSessionStore`
  - added `WorkspaceSnapshotV1`, `WorkspaceBranchSnapshot`, `WorkspacePreferencesSnapshot`, and `InvestigationSessionResponse`
  - `GET /sessions/{id}` now returns:
    - `workspace`
    - `restore_state`
    - mirrored top-level `nodes` and `edges`
    - normalized legacy bootstrap state for old rows
  - `POST /sessions/{id}/snapshot` now:
    - accepts full workspace payloads
    - rejects mismatched `sessionId`
    - upgrades legacy `node_states` into a stored workspace snapshot
- Validation:
  - focused suite now passes at `77 passed, 0 failed`

### Wave 3

- Status: next recommended wave.
- Objective: complete end-to-end session authority so investigators stop depending on browser-local graph state.
- Problems addressed:
  - create-session still does not persist an initial authoritative workspace snapshot through the new store boundary
  - frontend restore still trusts local storage before the backend
  - frontend autosave does not use the server workspace contract
  - there is no explicit saved / save_failed session status in frontend state
- Files likely affected:
  - `src/trace_compiler/compiler.py`
  - `src/api/routers/graph.py`
  - `src/services/graph_sessions.py`
  - `frontend/app/src/App.tsx`
  - `frontend/app/src/workspacePersistence.ts`
  - `frontend/app/src/store/graphStore.ts`
  - `frontend/app/src/components/InvestigationGraph.tsx`
- Dependencies:
  - Wave 02 complete and green
- Risks:
  - backend/frontend contract cutover
  - autosave write volume
  - legacy session compatibility during restore
- Validation:
  - backend create/restore/save contract tests
  - frontend restore/autosave tests
  - manual refresh verification against server state
- Rollback:
  - preserve compatibility with legacy snapshots and avoid deleting local fallback until the server path is verified
- Why before later waves:
  - this is the main remaining investigator-truth failure

### Wave 4

- Status: deferred until end-to-end session authority is real.
- Objective: finish the remaining trust and release-gate work.
- Problems addressed:
  - mounted bridge polling in the active inspector path
  - empty-state honesty from Phase 3
  - doc drift in `memory.md`, `lessons.md`, `todo.md`, README, and SECURITY
  - perf/resilience and release evidence gaps
- Files likely affected:
  - `frontend/app/src/components/GraphInspectorPanel.tsx`
  - `frontend/app/src/components/BridgeHopDrawer.tsx`
  - `frontend/app/src/hooks/useBridgeHopPoller.ts`
  - `src/trace_compiler/compiler.py`
  - `tasks/memory.md`
  - `tasks/lessons.md`
  - `tasks/todo.md`
  - `README.md`
  - `SECURITY.md`
- Dependencies:
  - Wave 03 complete and green
- Risks:
  - poller cancellation and stale state updates
  - perf ceilings exposed by real autosave and polling traffic
- Validation:
  - mounted bridge polling tests
  - empty-state regressions
  - perf probes and release packet evidence

## Recommended Order

1. Wave 1 — complete
2. Wave 2 — complete
3. Wave 3 — end-to-end server session authority cutover
4. Wave 4 — mounted bridge polling, empty-state honesty, docs, perf evidence, release packet

## Key Risks

- Treating the new backend workspace contract as “done” before the frontend actually uses it.
- Bundling bridge polling into the frontend authority cutover and turning the next wave into an oversized change set.
- Breaking legacy restore behavior while moving create/save/restore onto the server-backed workspace path.

## Rollback Notes

- Rolling back Wave 02 would knowingly reintroduce a fake restore contract and legacy-only snapshot saves.
- Wave 03 needs compatibility-aware rollback because it crosses the backend/frontend boundary.
- Wave 04 has no schema risk, but it can still expose perf or UX regressions that block release confidence.
