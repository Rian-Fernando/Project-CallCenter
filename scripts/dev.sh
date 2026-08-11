#!/usr/bin/env bash
# Start the backend and frontend together. Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "Creating .env from .env.example"; cp .env.example .env; }

if ! curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "WARNING: Ollama is not responding on port 11434."
  echo "         Start it with:  brew services start ollama"
  echo
fi

cleanup() { echo; echo "Stopping..."; kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "Backend  -> http://127.0.0.1:8000  (docs at /docs)"
( cd backend && ./.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 ) &

sleep 2
echo "Frontend -> http://localhost:5173"
( cd frontend && npm run dev ) &

wait
