#!/usr/bin/env python3
"""Copy the graph extraction surface from the current worktree into a target repo."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/split/graph-paths.txt"
IGNORED_PARTS = {
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}
IGNORED_SUFFIXES = {
    ".pyc",
    ".pyo",
}


def should_skip(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts) or path.suffix in IGNORED_SUFFIXES


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if should_skip(path):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            copy_file(path, target)


def sync_entry(relative_path: str, target_root: Path) -> None:
    source = ROOT / relative_path
    if not source.exists():
        return

    destination = target_root / relative_path
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        copy_tree(source, destination)
    else:
        copy_file(source, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("target_repo", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_root = args.target_repo.resolve()
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        relative_path = line.strip()
        if not relative_path or relative_path.startswith("#"):
            continue
        sync_entry(relative_path, target_root)


if __name__ == "__main__":
    main()
