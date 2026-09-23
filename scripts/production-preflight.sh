#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"

cd "$ROOT"
"$PY" -m pytest -q tests
"$PY" -m compileall -q src workers
git diff --check

if grep -RInE '(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)' \
  --exclude='*.example' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=data .; then
  echo "PREFLIGHT=FAIL possible credential material found" >&2
  exit 1
fi

echo "PREFLIGHT=PASS"
echo "LIVE_TRADING=OFF"
echo "PRODUCTION_CUTOVER=NOT_PERFORMED"
