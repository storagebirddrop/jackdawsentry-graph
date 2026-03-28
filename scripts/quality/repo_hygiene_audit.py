#!/usr/bin/env python3
"""Fail when internal-only files are tracked in the public repository."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TRACKED_PATTERNS: dict[str, str] = {
    "tasks/**": "internal working notes should not be tracked in the public repo",
    "artifacts/**": "raw artifacts should not be tracked in the public repo",
    "docs/drills/archive/**": "archived drill evidence should remain out of the public repo",
    "docs/drills/runs/**": "internal drill runs should remain out of the public repo",
    "docs/drills/DRILL_FRAMEWORK.md": "internal drill framework should not be tracked publicly",
    "docs/drills/GRAPH_TRUTHFULNESS_AND_INVESTIGATOR_MISINTERPRETATION_DRILL.md": (
        "internal drill prompt/spec should not be tracked publicly"
    ),
    "*.log": "raw logs should not be tracked in the public repo",
}

ALLOWED_TRACKED_PATHS = {
    "docs/drills/README.md",
    "docs/drills/runs/.gitkeep",
    "docs/drills/templates/DRILL_RUN_TEMPLATE.md",
}


def iter_tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=False,
    )
    return [path.decode("utf-8") for path in result.stdout.split(b"\x00") if path]


def find_forbidden_tracked_files(paths: Iterable[str]) -> list[str]:
    findings: list[str] = []
    for path in sorted(paths):
        if path in ALLOWED_TRACKED_PATHS:
            continue
        for pattern, reason in FORBIDDEN_TRACKED_PATTERNS.items():
            if fnmatch.fnmatch(path, pattern):
                findings.append(f"{path}: {reason}")
                break
    return findings


def main() -> int:
    findings = find_forbidden_tracked_files(iter_tracked_files())
    if findings:
        print("Repo hygiene audit failed:")
        for finding in findings:
            print(f" - {finding}")
        return 1

    print("Repo hygiene audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
