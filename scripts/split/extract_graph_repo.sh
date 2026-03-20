#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /absolute/path/to/jackdawsentry-graph"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required"
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if command -v git-filter-repo >/dev/null 2>&1; then
  GIT_FILTER_REPO_BIN="git-filter-repo"
elif [ -x "$ROOT/.venv/bin/git-filter-repo" ]; then
  GIT_FILTER_REPO_BIN="$ROOT/.venv/bin/git-filter-repo"
elif git filter-repo --help >/dev/null 2>&1; then
  GIT_FILTER_REPO_BIN="git filter-repo"
else
  echo "git filter-repo is required before extraction"
  exit 1
fi

OUTPUT_DIR="$1"
WORK_DIR="$(mktemp -d)"
MANIFEST="$ROOT/docs/split/graph-paths.txt"
TEMPLATE_DIR="$ROOT/repo-templates/jackdawsentry-graph"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "Preparing filtered graph repo in $OUTPUT_DIR"
git clone --no-local "$ROOT" "$WORK_DIR/repo"
cd "$WORK_DIR/repo"

FILTER_ARGS=()
while IFS= read -r path; do
  [ -z "$path" ] && continue
  FILTER_ARGS+=("--path" "$path")
done < "$MANIFEST"

if [ "$GIT_FILTER_REPO_BIN" = "git filter-repo" ]; then
  git filter-repo --force "${FILTER_ARGS[@]}"
else
  "$GIT_FILTER_REPO_BIN" --force "${FILTER_ARGS[@]}"
fi

mkdir -p "$OUTPUT_DIR"
cp -R "$WORK_DIR/repo"/. "$OUTPUT_DIR/"
if [ -x "$ROOT/.venv/bin/python" ]; then
  "$ROOT/.venv/bin/python" "$ROOT/scripts/split/sync_graph_surface.py" "$OUTPUT_DIR"
elif command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/scripts/split/sync_graph_surface.py" "$OUTPUT_DIR"
fi
cp -R "$TEMPLATE_DIR"/. "$OUTPUT_DIR/"

cat <<EOF
Graph repo extracted to:
  $OUTPUT_DIR

Next steps:
  1. cd "$OUTPUT_DIR"
  2. python scripts/split/public_readiness_audit.py
  3. pytest tests/test_trace_compiler -q
  4. (cd frontend/app && npm run lint && npm run build)
  5. review git history and publish only after the public-readiness gate passes
EOF
