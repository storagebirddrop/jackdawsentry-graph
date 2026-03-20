# Phase 5 Security Backlog

## P0 Before Public Release

- [ ] Remove browser bearer-token auth entirely in favor of HttpOnly cookies, or document and accept the residual risk if JWT-in-browser remains.
- [ ] Remove legacy flat graph endpoints after the private embed path no longer relies on them.
- [ ] Decide whether cross-session expansion caching should remain shared or become user/session scoped.
- [ ] Add production-tested Redis HA / degradation policy for rate limiting and session hop allowlists.
- [ ] Tighten private embed CSP and static-page script hygiene if the private repo continues to expose graph surfaces.
- [ ] Add load tests for hostile hub expansion, bridge hotspots, and repeated hop polling.

## P1 Soon After P0

- [ ] Trim `requirements.docker.txt` to graph-only runtime dependencies and separate optional analysis extras.
- [ ] Add CodeQL and container image signing to public CI/CD.
- [ ] Add explicit per-case or per-tenant isolation once case-bound investigations become a public feature.
- [ ] Add admin-visible rate-limit dashboards and alerts for abuse spikes.
- [ ] Revisit JWT lifetime and refresh-token posture after real analyst workflow observations.

## Private Embed Containment Mirror

- [ ] Keep the private repo graph surface internal-only until the public graph hardening wave is complete.
- [ ] Mirror auth fail-closed, docs/metrics gating, and distributed rate limiting into the private embed path.
- [ ] Remove broad host exposure for Neo4j/Postgres/Redis/Prometheus/Grafana in private Compose defaults.
