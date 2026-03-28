# Drill Records

This directory is the durable home for implementation drills, hostile reviews,
release gates, and similar execution runs.

Use this layout:

- `templates/` for reusable run scaffolds
- `runs/YYYY-MM-DD-slug/` for an active drill in progress
- `archive/YYYY-MM-DD-slug/` for a completed drill with frozen records

Rules for future drills:

- keep root-level product docs focused on durable repository material
- do not create new root-level `PHASE*`, `WAVE_*`, `FINAL_*`, `MASTER_*`, or
  `NEXT_WAVE_HANDOFF.md` files
- keep raw run records in `records/`
- keep screenshots, traces, JSON payload captures, and repro notes in
  `artifacts/`
- if a run produces one durable outcome worth keeping, summarize it in
  `docs/releases/` instead of leaving the entire run log at repo root

To start a new run:

```bash
python scripts/dev/init_drill_run.py my-drill-slug
```
