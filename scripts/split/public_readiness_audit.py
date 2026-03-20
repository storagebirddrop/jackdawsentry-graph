#!/usr/bin/env python3
"""Static public-readiness audit for the future MIT graph repo surface."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SURFACE = [
    ROOT / "repo-templates/jackdawsentry-graph",
    ROOT / "frontend/app/src",
    ROOT / "frontend/app/index.html",
    ROOT / "frontend/app/README.md",
    ROOT / "frontend/app/package.json",
    ROOT / "frontend/graph-login.html",
]

FORBIDDEN_SNIPPETS = {
    "Admin123!@#": "default credential leaked into public surface",
    "/api/v1/setup/status": "public graph surface should not depend on setup wizard",
    "All rights reserved": "public graph surface should not use private copyright wording",
    "commercial licensing": "public graph surface should not include private licensing copy",
    "enterprise compliance platform": "public graph surface should describe the standalone graph product",
}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for entry in PUBLIC_SURFACE:
        if entry.is_dir():
            files.extend(
                path
                for path in entry.rglob("*")
                if path.is_file() and "node_modules" not in path.parts and "dist" not in path.parts
            )
        elif entry.is_file():
            files.append(entry)
    return files


def main() -> int:
    findings: list[str] = []
    for path in iter_files():
        if path.suffix in {".png", ".ico"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for snippet, reason in FORBIDDEN_SNIPPETS.items():
            if snippet in text:
                findings.append(f"{path.relative_to(ROOT)}: {reason}")

    if findings:
        print("Public-readiness audit failed:")
        for finding in findings:
            print(f" - {finding}")
        return 1

    print("Public-readiness audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
