# 2026-03 Public Release Summary

Jackdaw Sentry Graph reached `Ready for public release` in the 2026-03 release
sequence.

Release basis:

- backend-owned session restore and autosave guardrails are in place
- mounted bridge polling is owned by the active investigator path
- directional empty-state honesty is implemented in the backend contract
- runtime hardening keeps `/docs` and `/openapi.json` disabled by default
- focused regression coverage and the frontend production build passed in the
  final gate
- browser artifacts were captured for:
  - mounted bridge polling in the active investigator UI
  - the Wave 04 empty-state notice in the active investigator UI

Durable operator notes:

- keep the focused regression suite green
- keep `/docs` and `/openapi.json` disabled by default
- preserve recent-session restore discovery, `restore_state`, snapshot revision
  conflict protection, mounted bridge polling ownership, and directional
  empty-state honesty
- monitor rollout health, `5xx` rates on `/api/v1/graph/sessions/recent`, stale
  snapshot conflicts, login failures, and protected-endpoint `429` rates

Supporting raw evidence is archived in:

- `docs/drills/archive/2026-03-release-readiness/records/`
- `docs/drills/archive/2026-03-release-readiness/artifacts/browser/`
