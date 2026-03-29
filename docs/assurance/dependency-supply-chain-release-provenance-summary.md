# Dependency / Supply-Chain / Release Provenance Assurance Summary

## Scope

This assurance review focused on whether the standalone graph product can become unsafe or untrustworthy because dependency resolution, build inputs, packaging outputs, container images, or served release artifacts drift away from what the repository intends to ship.

## What Was Reviewed

- Dependency locking and drift across the reviewed Python runtime, test, and frontend trees
- Build reproducibility and artifact provenance for the reviewed Docker/compose release path
- Frontend asset provenance for the compose-served `/app/` path
- Container and compose provenance on the reviewed runtime stack
- CI and release-path integrity for dependency audit, SBOM, and provenance checks
- Generated asset and served-artifact trust on the reviewed frontend path

## Major Issues Found And Addressed

- Observed in code: the most important confirmed provenance blocker was that the compose-served frontend previously depended on ignored local `frontend/app/dist` state.
- Observed in code: the reviewed Docker/compose path now packages the frontend bundle into a dedicated `graph-nginx` image, pins the reviewed container/base-image inputs by digest, and moves the trusted Python runtime path onto one exact `requirements.release.txt` manifest used by Docker, tests, and security auditing.
- Observed in executed tests/build/lint/typecheck: the previously reproduced frontend audit findings were cleared by a lockfile-only update.

## What Is Now Proven

- Observed in code: the reviewed compose-served `/app/` path no longer depends on a host-mounted frontend tree.
- Observed in executed tests/build/lint/typecheck: the frontend lock/install/build path still succeeds, and `npm audit --audit-level=high` now reports `found 0 vulnerabilities`.
- Observed in executed tests/build/lint/typecheck: the exact `requirements.release.txt` manifest resolves successfully and is now the reviewed Docker/test/security runtime surface.
- Observed in code: the reviewed Docker and compose path now pins Python, node, nginx, Neo4j, Postgres, and Redis inputs by digest.
- Observed in executed tests/build/lint/typecheck: the reviewed `graph-nginx` and `graph-api` image build path succeeds after the provenance remediation.
- Observed in runtime/repro: the built nginx image contains the packaged frontend assets at the served nginx path.
- Observed in runtime/repro: the built API image reports the expected pinned `fastapi`, `pydantic`, and `httpx` versions.
- Inferred from evidence: no material provenance blocker currently remains on the reviewed Docker/compose release path.

## Residual Risks And Limitations

- Inferred from evidence: the reviewed Python runtime path is materially tighter than before, but it still uses an exact-version manifest rather than a hash-locked install flow.
- Observed in runtime/repro: Python wheel publication remains weaker than the hardened Docker/compose path because wheel metadata and bit-for-bit wheel reproducibility were not remediated in this review.
- Inferred from evidence: the current repository now has a static release-provenance audit, but the reviewed GitHub workflow surface still does not itself prove the full image-build path end to end.
- Claimed in docs/history but not yet verified: signed or attested release outputs for the reviewed Docker/compose path.

## Explicit Non-Claims

This summary does not claim:

- end-to-end signed or attested release provenance for the reviewed compose path
- hash-locked Python runtime reproducibility
- full trustworthiness for wheel publication or every possible distribution surface
- global supply-chain closure beyond the reviewed Docker/compose release path
- cross-platform build reproducibility beyond the reviewed build and image checks performed here

## Maintenance Expectations

Revisit this assurance area after:

- dependency upgrades
- build or release-path changes
- Dockerfile or compose changes
- CI or audit workflow changes
- frontend asset packaging changes

## Repository Note

This repository intentionally keeps the public assurance summary concise. The full internal drill chain, handoff records, and raw provenance artifacts are not included in the public docs tree by default.
