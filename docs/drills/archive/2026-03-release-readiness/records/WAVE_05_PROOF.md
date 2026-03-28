# Wave 05 Proof

## Claims Under Verification

- Prior-wave guardrails remained present in the repo before Wave 5 edits.
- This run started as Wave 5 cleanup/hardening work but had to remediate two
  live rollout issues exposed by the rebuilt stack.
- Docs and security guidance are now aligned with the shipped session /
  bridge / empty-state behavior.
- The dead `BridgeHopDrawer.tsx` path is retired.
- The compose stack now boots from repo-owned mode flags instead of ambient
  shell `DEBUG`.
- Live `/api/v1/graph/sessions/recent` no longer 500s on UUID rows.
- The focused regression suite, build, audits, and rebuilt-stack runtime
  session probe all passed after the fixes.

## Evidence Matrix

| Claim | Evidence Label | Exact Evidence | File Path / Test / Command / Repro Source | Strength Of Proof | Remaining Uncertainty |
|---|---|---|---|---|---|
| Wave 02 session-authority guardrails remained present before Wave 5 edits | Observed in code | `GET /sessions/recent`, surfaced `restore_state`, and stale snapshot revision conflict handling were still present | `src/api/routers/graph.py`, `src/services/graph_sessions.py`, `frontend/app/src/App.tsx`, `frontend/app/src/api/client.ts` | High | No separate pre-edit runtime probe was captured before the first rebuild |
| Wave 03 mounted bridge polling remained present before Wave 5 edits | Observed in code | `useBridgeHopPoller` remained wired through `InvestigationGraph` and `GraphInspectorPanel`; store still exposed `updateBridgeHopStatus` | `frontend/app/src/components/InvestigationGraph.tsx`, `frontend/app/src/components/GraphInspectorPanel.tsx`, `frontend/app/src/store/graphStore.ts` | High | No live browser click-trace was executed in this run |
| The run could not stay cleanup-only because the first rebuild exposed a live compose boot failure | Observed in runtime/repro | Rebuilt stack served `502` from `/health`; API logs showed `DEBUG=release` and `GRAPH_AUTH_DISABLED=true` validation failure | `curl http://localhost:8081/health`; `docker logs jackdawsentry_graph_api` during first rebuild | High | The failure depended on the caller shell env, not a committed `.env` value |
| Compose hardening now isolates graph mode flags from ambient shell `DEBUG` | Observed in code | `graph-api` now uses `env_file: ./.env` and no longer interpolates `DEBUG` / `GRAPH_AUTH_DISABLED` directly from shell variables | `docker-compose.graph.yml` | High | Other generic shell env names outside this pair are still possible in theory |
| The compose hardening fix actually took effect on the live rebuilt stack | Observed in runtime/repro | `docker inspect jackdawsentry_graph_api` showed `DEBUG=true` and `GRAPH_AUTH_DISABLED=true`; `/health` returned `200` after rebuild | `docker inspect jackdawsentry_graph_api --format '{{json .Config.Env}}'`; `curl http://localhost:8081/health` | High | This proves the current local rebuild, not every possible deployment path |
| Live `/sessions/recent` was broken before the final fix | Observed in runtime/repro | `/api/v1/graph/sessions/recent?limit=1` returned `500`; API logs showed `RecentSessionsResponse` rejected UUID `session_id` values | `curl http://localhost:8081/api/v1/graph/sessions/recent?limit=1`; `docker logs jackdawsentry_graph_api` | High | The regression surfaced only on live asyncpg rows, not mocked test rows |
| The live `/sessions/recent` regression is fixed in code | Observed in code | `GraphSessionStore.list_recent_sessions()` now normalizes UUIDs to strings through `RecentSessionSummary` | `src/services/graph_sessions.py` | High | None for the normalization path |
| The test suite now covers the real UUID row shape behind the live `/sessions/recent` failure | Observed in executed tests/build/lint/typecheck | recent-session endpoint test now uses `UUID(...)`; focused suite passed | `tests/test_trace_compiler/test_session_endpoints.py`; focused pytest command | High | Test still mocks the router dependency rather than hitting a live DB |
| Dead bridge-drawer ownership is retired | Observed in code | `frontend/app/src/components/BridgeHopDrawer.tsx` no longer exists; browser-surface test asserts absence | file deletion plus `tests/test_api/test_browser_surface.py` | High | None for current repo state |
| Docs now describe shipped session-authority / bridge / empty-state behavior | Observed in code | updated durable decisions, lessons, work queue, README, and SECURITY policy | `tasks/memory.md`, `tasks/lessons.md`, `tasks/todo.md`, `README.md`, `SECURITY.md` | High | Docs do not themselves prove runtime behavior |
| Focused regression suite stayed green after Wave 5 fixes | Observed in executed tests/build/lint/typecheck | `91 passed, 0 failed` | `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q` | High | Focused suite is not exhaustive end-to-end browser coverage |
| Frontend still builds after deleting the dead bridge drawer and updating docs/tests | Observed in executed tests/build/lint/typecheck | production build passed | `npm run build` in `frontend/app` | High | Build success does not prove every runtime UI path |
| Repo-native audits still pass after Wave 5 changes | Observed in executed tests/build/lint/typecheck | `Boundary audit passed.` and `Public-readiness audit passed.` | `.venv/bin/python scripts/quality/boundary_audit.py`; `.venv/bin/python scripts/quality/public_readiness_audit.py` | High | These audits are narrower than a full hostile release review |
| Live session-authority flow works on the rebuilt stack | Observed in runtime/repro | create `200`; recent `200`; restore `200`; first snapshot save with incremented revision `200`; repeated same revision `409` | custom runtime probe against `http://localhost:8081` executed in this run | High | Probe used an auth-disabled local stack and a simple seed address, not a browser flow |
| Public docs endpoints remain disabled on the rebuilt stack | Observed in runtime/repro | `/docs` and `/openapi.json` both returned `404` | `curl http://localhost:8081/docs`; `curl http://localhost:8081/openapi.json` | High | No auth-enabled release candidate was exercised |
| A representative local dataset exists for limited perf interpretation | Observed in runtime/repro | live DB/Neo4j counts: `raw_transactions~=186`, `raw_token_transfers~=564`, `graph_sessions=94`, `bridge_correlations=7`, `nodes=634821`, `relationships=729389` | custom asyncpg/Neo4j probe against local runtime in this run | Medium | This is a local dataset footprint, not a full auth-enabled perf probe |

## Prior-Wave Guardrails Rechecked

- Observed in code: recent-session restore discovery still exists.
- Observed in code: `restore_state` handling is still surfaced in the frontend.
- Observed in code: snapshot revision conflict protection is still present.
- Observed in code: mounted bridge polling still owns active bridge-hop freshness.
- Observed in code: Wave 04 indexed directional empty-state logic remains in
  the compiler.
- Observed in executed tests/build/lint/typecheck: the focused suite remained
  green after the Wave 5 fixes.

## Regressions Checked

- Observed in executed tests/build/lint/typecheck: focused regression suite
  passed (`91 passed, 0 failed`).
- Observed in executed tests/build/lint/typecheck: frontend build passed.
- Observed in executed tests/build/lint/typecheck: boundary and public-readiness
  audits passed.
- Observed in runtime/repro: rebuilt stack served `/health`, `/api/v1/status`,
  `/api/v1/graph/sessions/recent`, `GET /sessions/{id}`, and snapshot conflict
  responses as expected after the fixes.

## What Is Proven

- Observed in code: docs now match the shipped guardrails more closely than the
  pre-Wave-5 repo.
- Observed in code: dead bridge-drawer ownership is gone.
- Observed in code: recent-session UUID normalization is fixed at the source.
- Observed in executed tests/build/lint/typecheck: focused suite, build, and
  repo audits pass after the fixes.
- Observed in runtime/repro: the rebuilt stack boots with repo-owned mode flags
  and serves the live session-authority flow correctly.
- Observed in runtime/repro: public docs endpoints remain disabled on the
  rebuilt stack.

## What Is Only Inferred

- Inferred from evidence: the release candidate is materially stronger than the
  pre-Wave-5 state because docs, compose hardening, and live session-authority
  probes now line up.
- Inferred from evidence: deleting the dead `BridgeHopDrawer` path reduces
  ownership confusion without affecting active runtime behavior, because the
  mounted poller path still builds and the focused suite stayed green.

## What Is Still Unverified

- Claimed in docs/history but not yet verified: a live browser artifact proving
  the mounted bridge polling UI in action.
- Claimed in docs/history but not yet verified: a live browser artifact proving
  the Wave 04 empty-state notice text in the running UI.
- Claimed in docs/history but not yet verified: auth-enabled `live_abuse_probe`
  and `live_perf_probe` results for a release candidate with credentials.
- Observed in executed tests/build/lint/typecheck: the frontend build still
  reports large chunks after minification.

## Safe To Refresh The Release Gate? (Yes / No / Yes With Conditions)

Yes With Conditions

Conditions:
- keep the focused regression suite green
- preserve the Wave 02 session-authority guardrails, Wave 03 mounted bridge
  polling path, and Wave 04 empty-state honesty logic
- do not overclaim runtime/browser proof for bridge polling or empty-state UI
  notices until a concrete browser artifact exists
- treat auth-enabled `live_abuse_probe.py` and `live_perf_probe.py` as still
  required before any broader production claim beyond limited/internal release
