#!/usr/bin/env bash
# One-time setup: Python venv, dependencies, .env, frontend packages.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
echo "Using Python: $($PY --version)"

[ -d backend/.venv ] || "$PY" -m venv backend/.venv
./backend/.venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel
echo "Installing backend dependencies (this takes a minute)..."
./backend/.venv/bin/python -m pip install -q -r backend/requirements.txt

[ -f .env ] || cp .env.example .env

echo "Installing frontend dependencies..."
( cd frontend && npm install --silent )

cat <<'MSG'

Setup complete.

Next:
  1. brew install ollama && brew services start ollama
  2. ollama pull qwen3:8b && ollama pull nomic-embed-text
  3. ./scripts/crawl.sh      # fetch official Village pages (~2.5 min)
  4. ./scripts/ingest.sh     # build the search index
  5. ./scripts/dev.sh        # start backend + frontend

Then open http://localhost:5173
MSG
