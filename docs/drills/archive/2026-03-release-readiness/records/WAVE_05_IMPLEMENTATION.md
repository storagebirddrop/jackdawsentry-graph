# Wave 05 Implementation

## Objective

Finish the release-hardening wave by aligning repo docs with the shipped session
authority / bridge polling / empty-state behavior, retiring dead bridge-drawer
 ownership, rebuilding the live stack from current repo state, and collecting a
 release packet from real executed evidence.

## Why This Wave Next

- Observed in code: Waves 02, 03, and 04 were present in the repo at the start
  of this run.
- Observed in code: the recommended next work from
  `NEXT_WAVE_HANDOFF.md` was docs alignment, release evidence, and low-risk
  cleanup.
- Observed in runtime/repro: the first live rebuild in this run exposed two
  real rollout blockers that were not visible from static docs alone:
  - the graph API could fail to boot if an ambient shell exported
    `DEBUG=release`
  - `GET /api/v1/graph/sessions/recent` 500ed on live UUID rows
- Inferred from evidence: this run had to stay in Wave 5 scope, but it could
  not remain cleanup-only once the rebuilt runtime proved those rollout issues
  were real.

## Wave 05 Readiness Summary

- Observed in code: the Wave 02 session-authority guardrails were still present
  before editing:
  - backend recent-session discovery route
  - surfaced `restore_state`
  - snapshot revision conflict path
- Observed in code: the Wave 03 mounted bridge poller was still present in the
  active UI path.
- Observed in code: the Wave 04 empty-state honesty logic was still present in
  `src/trace_compiler/compiler.py`.
- Observed in executed tests/build/lint/typecheck: the focused regression suite
  and frontend production build were green before the rebuild attempt.
- Observed in runtime/repro: the repo was not fully clear for pure cleanup-only
  Wave 5 work because the rebuilt stack immediately exposed a compose boot
  issue and a live `/sessions/recent` regression.

## Execution Classification

Mixed cleanup/remediation due to repo/plan mismatch

## Files Changed

- `docker-compose.graph.yml`
- `src/services/graph_sessions.py`
- `src/api/routers/graph.py`
- `tests/test_api/test_browser_surface.py`
- `tests/test_trace_compiler/test_session_endpoints.py`
- `tasks/memory.md`
- `tasks/lessons.md`
- `tasks/todo.md`
- `README.md`
- `SECURITY.md`
- `frontend/app/src/components/BridgeHopDrawer.tsx` (deleted)
- `WAVE_05_IMPLEMENTATION.md`
- `WAVE_05_PROOF.md`
- `RELEASE_GATE_PACKET.md`
- `NEXT_WAVE_HANDOFF.md`
- `MASTER_EXECUTION_PLAN_UPDATED.md`

## Summary of Code Changes

- Observed in code: hardened `docker-compose.graph.yml` so the graph API now
  loads `DEBUG` / `GRAPH_AUTH_DISABLED` from repo-owned `.env` via `env_file`
  instead of inheriting those safety-critical flags from the caller shell.
- Observed in code: fixed `GraphSessionStore.list_recent_sessions()` in
  `src/services/graph_sessions.py` to normalize live UUID rows into string
  `session_id` values before building `RecentSessionsResponse`.
- Observed in code: removed stale inline “Phase 3 stub” docstrings from
  `src/api/routers/graph.py` so the public router comments match shipped
  behavior.
- Observed in code: retired the dead `frontend/app/src/components/BridgeHopDrawer.tsx`
  path after confirming the mounted polling owner is `useBridgeHopPoller` in
  the active inspector flow.

## Documentation Changes

- Observed in code: updated `tasks/memory.md` with durable decisions for:
  - backend-authoritative session restore/autosave
  - mounted bridge polling ownership
  - indexed directional empty-state honesty
- Observed in code: updated `tasks/lessons.md` with concrete lessons for:
  - backend-owned restore discovery
  - dead contract fields like `restore_state`
  - autosave revision protection
  - detached polling ownership
  - docker-compose shell env override drift
- Observed in code: updated `tasks/todo.md` so the session-contract hardening
  pass is recorded as complete and the remaining work is explicitly narrowed to
  post-release tech debt and stronger runtime proof.
- Observed in code: updated `README.md` to describe:
  - backend-owned session restore/autosave
  - `legacy_bootstrap` behavior
  - the mounted bridge polling path
  - honest empty-state wording
  - auth-dependent vs auth-agnostic verification helpers
- Observed in code: expanded `SECURITY.md` to reflect the shipped security
  posture instead of leaving it as a placeholder policy stub.

## Tests Added or Updated

- Updated `tests/test_trace_compiler/test_session_endpoints.py`
  - the recent-session test now feeds a real `UUID` object instead of a string,
    matching the live asyncpg row shape that caused the runtime 500.
- Updated `tests/test_api/test_browser_surface.py`
  - added `test_dead_bridge_drawer_path_is_retired()` to lock in removal of the
    dead bridge-drawer polling path.

## Validation Performed

- Observed in executed tests/build/lint/typecheck:
  - `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
  - `npm run build` in `frontend/app`
  - `.venv/bin/python scripts/quality/boundary_audit.py`
  - `.venv/bin/python scripts/quality/public_readiness_audit.py`
- Observed in runtime/repro:
  - rebuilt the stack with `docker compose -f docker-compose.graph.yml up -d --build`
  - verified `/health`, `/api/v1/status`, `/api/v1/graph/sessions/recent`,
    `/docs`, `/openapi.json`
  - executed a live session-authority probe covering:
    - session create
    - recent-session discovery
    - session restore
    - successful snapshot save with an incremented revision
    - repeated stale snapshot write rejected with `409`
  - queried live dataset footprint from PostgreSQL and Neo4j

## Results

- Observed in executed tests/build/lint/typecheck: focused regression suite
  passed (`91 passed, 0 failed`).
- Observed in executed tests/build/lint/typecheck: frontend production build
  passed.
- Observed in executed tests/build/lint/typecheck: `boundary_audit.py` passed.
- Observed in executed tests/build/lint/typecheck: `public_readiness_audit.py`
  passed.
- Observed in runtime/repro: the first rebuild attempt failed because the
  container booted with `DEBUG=release`; this run fixed that compose hardening
  issue.
- Observed in runtime/repro: the first live `/sessions/recent` check returned
  `500`; this run fixed that live UUID normalization bug.
- Observed in runtime/repro: after the fixes, the rebuilt stack served:
  - `/health` → `200`
  - `/api/v1/status` → `200`
  - `/api/v1/graph/sessions/recent?limit=1` → `200`
  - `/docs` → `404`
  - `/openapi.json` → `404`
- Observed in runtime/repro: the live session-authority probe succeeded:
  - session create → `200` (`178.4 ms`)
  - session restore → `200` (`10.8 ms`)
  - first snapshot save with incremented revision → `200` (`335.7 ms`)
  - repeated stale snapshot save → `409` (`7.6 ms`)
- Observed in runtime/repro: the rebuilt local dataset footprint was:
  - PostgreSQL `raw_transactions` estimate: `186`
  - PostgreSQL `raw_token_transfers` estimate: `564`
  - PostgreSQL `graph_sessions`: `94`
  - PostgreSQL `bridge_correlations`: `7`
  - Neo4j nodes: `634821`
  - Neo4j relationships: `729389`

## Remaining Risks

- Observed in code: there is still no dedicated frontend runtime harness for
  bridge polling interactions or Wave 04 empty-state UI notices.
- Observed in runtime/repro: no live browser screenshot or manual UI artifact
  was captured for the mounted bridge polling path or the Wave 04 empty-state
  notice text.
- Observed in executed tests/build/lint/typecheck: the frontend build still
  emits large-chunk warnings.
- Observed in code: session creation persistence is still split between
  `TraceCompiler.create_session` and `GraphSessionStore`.
- Inferred from evidence: auth-enabled release confidence is still weaker than
  internal auth-disabled confidence because `live_abuse_probe.py` and
  `live_perf_probe.py` were not executed against a credentialed stack in this
  run.

## Final Follow-up Needed

- Refresh the hostile release gate against this rebuilt-stack evidence packet.
- Capture explicit browser/runtime artifacts for:
  - mounted bridge polling in the live UI
  - the Wave 04 empty-state notice in the live UI
- Run auth-enabled `live_abuse_probe.py` and `live_perf_probe.py` on a release
  candidate before claiming broader production readiness.
- Treat any further work as post-Wave-05 release hardening or deferred tech
  debt, not a new correctness wave, unless new executed evidence reproduces a
  regression.
