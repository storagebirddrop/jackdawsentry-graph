# Work Queue

Use this file for active graph-product plans, acceptance criteria, and verification steps.

## Script Layout Cleanup [IN PROGRESS]

Goal:
- remove split-era script naming from the public graph repo and keep only the
  product-facing operational tooling

Acceptance criteria:
- [x] useful scripts moved under `scripts/dev`, `scripts/quality`, or
      `scripts/branding`
- [x] extraction-only scripts removed from the public graph repo
- [x] docs point to the new script locations
- [x] stale split-era wording is removed from product-facing ops files

## Repo Hardening Pass [COMPLETE]

Goal:
- make the public repo read as the canonical graph-product repo, not an
  extraction artifact

Acceptance criteria:
- [x] root guidance files clearly state this repo owns new graph work
- [x] memory file captures the private-repo boundary rule
- [x] stale split-era language is removed from core docs
