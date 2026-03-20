#!/usr/bin/env python3
"""Boundary audit for the split-first program."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GRAPH_SCOPE = [
    ROOT / "frontend/app/src",
    ROOT / "src/trace_compiler",
    ROOT / "src/api/graph_app.py",
]

FORBIDDEN_IMPORT_PREFIXES = [
    "src.api.routers.compliance",
    "src.api.routers.investigations",
    "src.api.routers.reports",
    "src.api.routers.admin",
    "src.api.routers.teams",
    "src.api.routers.workflows",
    "src.api.routers.setup",
    "src.compliance",
]

PY_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(?P<module>[a-zA-Z0-9_\.]+)",
    re.MULTILINE,
)
TS_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:type\s+)?(?:.+?\s+from\s+)?['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for entry in GRAPH_SCOPE:
        if entry.is_dir():
            files.extend(
                path for path in entry.rglob("*")
                if path.suffix in {".py", ".ts", ".tsx"}
            )
        elif entry.is_file():
            files.append(entry)
    return files


def extract_imports(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return [match.group("module") for match in PY_IMPORT_RE.finditer(text)]
    if path.suffix in {".ts", ".tsx"}:
        return [match.group("module") for match in TS_IMPORT_RE.finditer(text)]
    return []


def main() -> int:
    violations: list[tuple[Path, str]] = []
    for path in iter_files():
        for module in extract_imports(path):
            if any(module.startswith(prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES):
                violations.append((path.relative_to(ROOT), module))

    if violations:
        print("Boundary audit failed:")
        for path, module in violations:
            print(f" - {path}: imports forbidden private module {module}")
        return 1

    print("Boundary audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
