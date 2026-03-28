# Final Public Release Packet

## Current Release Recommendation

Ready for public release

## Scope Reviewed

- Wave 02 session-authority guardrails
- Wave 03 mounted bridge polling path
- Wave 04 empty-state honesty
- Wave 05/06 release hardening and auth-enabled release evidence
- final browser-artifact closure evidence

## Guardrails That Must Not Regress

- recent-session restore discovery
- surfaced `restore_state`
- snapshot revision conflict protection
- mounted bridge polling in the active investigator path
- indexed directional empty-state honesty
- `/docs` disabled by default
- `/openapi.json` disabled by default

## Correctness Status

- Observed in code: the prior correctness waves remain present in the repo.
- Observed in executed tests/build/lint/typecheck: the focused regression suite passed (`91 passed, 0 failed`) in this final gate run.
- Inferred from evidence: no current correctness blocker undercuts the public-release claim.

## Session-Authority Status

- Observed in code: backend recent-session restore discovery, `restore_state`, and stale snapshot revision protection remain present.
- Observed in executed tests/build/lint/typecheck: session endpoint, security, persistence, and browser-surface coverage remain green.

## Bridge Polling Status

- Observed in code: mounted bridge polling remains owned by `useBridgeHopPoller` in the active investigator path.
- Observed in browser artifact: `mounted_bridge_polling.png` shows the selected pending `deBridge` hop in the active inspector with `Polling every 30s` and `Last checked`.
- Observed in browser artifact: `mounted_bridge_polling_status.json` matches that screenshot with a real browser-observed `200` hop-status response.

## Empty-State Honesty Status

- Observed in code: the compiler still distinguishes indexed requested-direction and indexed opposite-direction empty-state cases.
- Observed in browser artifact: `wave04_empty_state_notice.png` shows the honest `Investigation note` banner rendered in the active UI.
- Observed in browser artifact: `wave04_empty_state_notice_response.json` matches that banner with `reason = "indexed_activity_in_other_direction"`.

## Documentation Alignment Status

- Observed in code: Wave 05 doc alignment remains present in `README.md`, `SECURITY.md`, `tasks/memory.md`, `tasks/lessons.md`, and `tasks/todo.md`.

## Auth-Enabled Abuse Probe Status

- Claimed in docs/history but not yet verified: Passed in Wave 06.
- Inferred from evidence: still valid for the current release decision because no guardrail regression or current runtime blocker was found.

## Auth-Enabled Perf Probe Status

- Claimed in docs/history but not yet verified: Passed in Wave 06, including an Ethereum-seeded graph-growth case.
- Inferred from evidence: still valid for the current release decision because no guardrail regression or current runtime blocker was found.

## Browser Artifact Status

- Observed in browser artifact: mounted bridge polling artifact captured.
- Observed in browser artifact: Wave 04 empty-state notice artifact captured.
- Inferred from evidence: the browser-artifact gap is now closed.

## Perf / Rollout Evidence

- Observed in executed tests/build/lint/typecheck: current focused regression suite passed.
- Observed in executed tests/build/lint/typecheck: current frontend production build passed.
- Claimed in docs/history but not yet verified: Wave 06 auth-enabled perf evidence recorded passed local release-candidate probes and non-zero graph growth on the Ethereum-seeded run.

## Monitoring / Rollback Readiness

- Observed in runtime/repro: `/health` currently returns `200`.
- Observed in code: rollback guidance from prior packets still preserves the session-authority, bridge-polling, and empty-state guardrails.
- Inferred from evidence: public rollout remains monitorable through:
  - `/health`
  - `/api/v1/status`
  - `/api/v1/graph/sessions/recent`
  - `409 Stale workspace snapshot revision` rates
  - auth login failures and `429` rates on protected graph endpoints

## Known Limitations

- Observed in executed tests/build/lint/typecheck: the frontend build still emits large-chunk warnings.
- Observed in code: session creation persistence remains split between `TraceCompiler.create_session` and `GraphSessionStore`.

## Open Risks

- Claimed in docs/history but not yet verified: the auth-enabled abuse/perf probes were not rerun in this exact final gate run.
- Claimed in docs/history but not yet verified: the browser artifacts were captured on the local auth-disabled stack rather than an authenticated browser session.
- Inferred from evidence: these are non-blocking because auth-enabled runtime evidence already exists and the current code/runtime baseline did not regress.

## Explicit Non-Claims

- This packet does **not** claim a fresh rerun of the auth-enabled abuse probe in this exact gate run.
- This packet does **not** claim a fresh rerun of the auth-enabled perf probe in this exact gate run.
- This packet does **not** claim a production-scale perf characterization beyond the existing local release-candidate evidence.
- This packet does **not** claim that the current local fixture dataset represents every deployment footprint.

## Release Decision

Ready for public release

## Conditions On Release

- keep the focused regression suite green
- keep `/docs` and `/openapi.json` disabled by default
- preserve recent-session restore discovery, `restore_state`, and snapshot revision conflict handling
- preserve mounted bridge polling ownership in the active investigator path
- preserve indexed directional empty-state honesty

## Required Post-Release Monitoring

- monitor `/health` and `/api/v1/status`
- monitor `5xx` rates on `/api/v1/graph/sessions/recent`
- monitor `409 Stale workspace snapshot revision` rates
- monitor login failures and `429` rates on protected graph endpoints
- monitor investigator reports for bridge-poll refresh failures or misleading empty-state notices
