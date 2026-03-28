#!/usr/bin/env python3
"""Scaffold a dated drill run directory under docs/drills/runs/."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "docs" / "drills" / "runs"
TEMPLATE_PATH = REPO_ROOT / "docs" / "drills" / "templates" / "DRILL_RUN_TEMPLATE.md"


def _slugify(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    if not slug:
        raise ValueError("slug must contain at least one alphanumeric character")
    return slug


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a new drill run directory under docs/drills/runs/."
    )
    parser.add_argument("slug", help="Short drill identifier, for example public-release-gate")
    parser.add_argument(
        "--date",
        dest="run_date",
        default=date.today().isoformat(),
        help="Run date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    slug = _slugify(args.slug)
    run_dir = RUNS_ROOT / f"{args.run_date}-{slug}"
    records_dir = run_dir / "records"
    artifacts_dir = run_dir / "artifacts"
    browser_dir = artifacts_dir / "browser"
    runtime_dir = artifacts_dir / "runtime"

    if run_dir.exists():
        print(f"Run already exists: {run_dir}", file=sys.stderr)
        return 1

    records_dir.mkdir(parents=True)
    browser_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)

    if TEMPLATE_PATH.exists():
        shutil.copyfile(TEMPLATE_PATH, run_dir / "README.md")
    else:
        (run_dir / "README.md").write_text("# Drill Run\n", encoding="utf-8")

    print(f"Created drill scaffold: {run_dir}")
    print(f"- records: {records_dir}")
    print(f"- browser artifacts: {browser_dir}")
    print(f"- runtime artifacts: {runtime_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
