# Public Release Gate Packet

## Current Release Recommendation

Ready for limited/beta external release only

## Scope Reviewed

- Wave 02 session-authority guardrails
- Wave 03 mounted bridge polling path
- Wave 04 empty-state honesty
- Wave 05 release hardening and docs/security posture
- Wave 06 auth-enabled probe evidence and public-release blockers

## Guardrails That Must Not Regress

- recent-session restore discovery
- surfaced `restore_state`
- snapshot revision conflict protection
- mounted bridge polling in the active investigator path
- indexed directional empty-state honesty
- `/docs` disabled by default
- `/openapi.json` disabled by default

## Correctness Status

- Observed in code: prior correctness waves remain present in the repo.
- Observed in executed tests/build/lint/typecheck: the focused regression suite
  passed (`91 passed, 0 failed`).
- Observed in runtime/repro: auth-enabled abuse and perf probes both passed on
  a temporary credentialed local candidate.

## Session-Authority Status

- Observed in code: backend recent-session restore discovery, `restore_state`,
  and revision conflict protection remain present.
- Observed in executed tests/build/lint/typecheck: session endpoint/security and
  persistence tests remain green.
- Observed in runtime/repro: the auth-enabled abuse/perf probes successfully
  created graph sessions against the credentialed candidate.

## Bridge Polling Status

- Observed in code: mounted bridge polling remains owned by
  `useBridgeHopPoller` in the active investigator path.
- Observed in executed tests/build/lint/typecheck: browser-surface tests and
  frontend build remain green.
- Claimed in docs/history but not yet verified: no concrete browser artifact was
  captured in this run showing the mounted poller updating the live UI.

## Empty-State Honesty Status

- Observed in code: the compiler still distinguishes requested-direction and
  opposite-direction indexed activity.
- Observed in executed tests/build/lint/typecheck: compiler stub regressions
  covering those branches remain green.
- Claimed in docs/history but not yet verified: no concrete browser artifact was
  captured in this run for the rendered empty-state notice.

## Documentation Alignment Status

- Observed in code: Wave 05 doc alignment remains present in
  `tasks/memory.md`, `tasks/lessons.md`, `tasks/todo.md`, `README.md`, and
  `SECURITY.md`.
- Observed in code: the repo now also includes the documented
  `scripts/dev/create_user.py` helper for auth-enabled probe setup.

## Auth-Enabled Abuse Probe Status

- Observed in runtime/repro: Passed
- Exact evidence:
  - `DEBUG=true .venv/bin/python scripts/dev/create_user.py --username wave6_probe --password 'Wave6Probe!234' --email wave6_probe@local.invalid --role analyst`
  - `DEBUG=true .venv/bin/python scripts/quality/live_abuse_probe.py --username wave6_probe --password 'Wave6Probe!234'`
- Probe result summary:
  - `/health` `200`
  - `/docs` `404`
  - login shell `200`
  - login API `200`
  - session create `200`
  - expand abuse path hit `429`
  - hop-status abuse path hit `429`

## Auth-Enabled Perf Probe Status

- Observed in runtime/repro: Passed
- Exact evidence:
  - `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234'`
  - `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234' --seed-address 0xdac17f958d2ee523a2206206994597c13d831ec7 --seed-chain ethereum --iterations 3`
- Probe result summary:
  - control-plane perf path passed on the auth-enabled candidate
  - explicit Ethereum seed produced non-zero `expand_neighbors` graph growth
  - no bridge-hop node was surfaced in the captured perf runs

## Browser Artifact Status

- Observed in runtime/repro: No concrete browser artifacts were produced in
  this run.
- Observed in runtime/repro: Playwright could not launch because Chrome was not
  installed, and the install path could not complete due non-interactive sudo
  restrictions.

## Perf / Rollout Evidence

- Observed in executed tests/build/lint/typecheck:
  - `boundary_audit.py` passed
  - `public_readiness_audit.py` passed
  - frontend production build passed
- Observed in runtime/repro:
  - auth-enabled `live_abuse_probe.py` passed
  - auth-enabled `live_perf_probe.py` passed
  - explicit Ethereum-seeded perf run produced:
    - `session-create` `200` in `2348.3 ms`
    - `expand_next` `200` with median `241.4 ms`, p95 `856.0 ms`
    - `expand_neighbors` `200` with median `7.8 ms`, p95 `74.6 ms`
  - dataset footprint from the perf probe:
    - PostgreSQL `raw_transactions~=186`
    - PostgreSQL `raw_token_transfers~=564`
    - PostgreSQL `graph_sessions=96`
    - PostgreSQL `bridge_correlations=7`
    - Neo4j `nodes=634821`
    - Neo4j `relationships=729389`

## Monitoring / Rollback Readiness

- Observed in code: rollback guidance from prior waves still preserves the
  session-authority, bridge-polling, and empty-state guardrails.
- Inferred from evidence: limited/beta external rollout is monitorable if the
  team tracks:
  - `/health`
  - `/api/v1/status`
  - `/api/v1/graph/sessions/recent`
  - `409 Stale workspace snapshot revision` rates
  - auth login failures and `429` rates on protected graph endpoints

## Known Limitations

- No browser artifact for mounted bridge polling.
- No browser artifact for the Wave 04 empty-state notice.
- Frontend build still reports large chunks after minification.
- Session creation persistence remains split between `TraceCompiler` and
  `GraphSessionStore`.

## Open Risks

- Browser-level investigator-facing proof is still weaker than the API/runtime
  proof.
- Broader public-release confidence still depends on the missing browser
  artifacts, not only on auth-enabled probes.
- The auth-enabled evidence is strong for a local release candidate, but not
  yet the same as a production-scale public rollout proof set.

## Explicit Non-Claims

- This packet does **not** claim a browser artifact for mounted bridge polling.
- This packet does **not** claim a browser artifact for the rendered Wave 04
  empty-state notice.
- This packet does **not** claim unrestricted public-release readiness.
- This packet does **not** claim a full production-scale perf characterization.
- This packet does **not** claim that the current local dataset represents every
  external deployment footprint.

## Release Decision

Ready for limited/beta external release only

## Conditions On Release

- keep the focused regression suite green
- keep `/docs` and `/openapi.json` disabled by default
- preserve recent-session restore discovery, `restore_state`, and snapshot
  revision conflict handling
- preserve mounted bridge polling ownership in the active investigator path
- preserve indexed directional empty-state honesty
- do not market the current proof set as unrestricted public-release proof
  until concrete browser artifacts exist for:
  - mounted bridge polling
  - the Wave 04 empty-state notice

## Required Post-Release Monitoring

- monitor `/health` and `/api/v1/status`
- monitor login failures and `429` rates on:
  - `/api/v1/auth/login`
  - `POST /api/v1/graph/sessions/{id}/expand`
  - `GET /api/v1/graph/sessions/{id}/hops/{hop_id}/status`
- monitor `5xx` rates on `/api/v1/graph/sessions/recent`
- monitor `409 Stale workspace snapshot revision` rates to distinguish healthy
  revision protection from unexpected autosave churn
- capture concrete browser/runtime artifacts during the beta for:
  - mounted bridge polling
  - the Wave 04 empty-state notice

## Public-Release Exit Criteria Still Open

- browser artifact for mounted bridge polling in the active UI
- browser artifact for the Wave 04 empty-state notice in the active UI
- a refreshed public gate after those browser artifacts exist
