#!/usr/bin/env bash
# Start the backend and frontend together. Ctrl-C stops both.
#
# Safe to re-run: it reclaims its own ports first. A crashed or backgrounded
# run from earlier would otherwise leave uvicorn or vite holding 8000/5173, and
# the only symptom is "Address already in use" — which is a bad thing to be
# debugging minutes before a demo.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

[ -f .env ] || { echo "Creating .env from .env.example"; cp .env.example .env; }

# --- reclaim ports ---------------------------------------------------------
reclaim() {
  local port="$1" label="$2" pids
  pids="$(lsof -ti:"$port" 2>/dev/null || true)"
  [ -z "$pids" ] && return 0

  echo "  Port $port is in use by: $(ps -p "$(echo "$pids" | head -1)" -o comm= 2>/dev/null)"
  # Only reclaim processes that belong to this project. Killing an unrelated
  # service because it happened to want the same port would be worse than
  # failing loudly.
  local mine=""
  for pid in $pids; do
    if ps -p "$pid" -o command= 2>/dev/null \
       | grep -qE 'uvicorn app\.main:app|[Vv]ite|node_modules/\.bin/vite'; then
      mine="$mine $pid"
    fi
  done

  if [ -z "$mine" ]; then
    echo "  Not one of ours — leaving it alone."
    echo "  Free it yourself, or run with a different port:"
    echo "     ${label}=<port> ./scripts/dev.sh"
    return 1
  fi

  echo "  Stopping our stale process(es):$mine"
  # shellcheck disable=SC2086
  kill $mine 2>/dev/null
  sleep 1
  # shellcheck disable=SC2086
  kill -9 $mine 2>/dev/null || true
  sleep 1
  return 0
}

echo "Checking ports..."
reclaim "$API_PORT" API_PORT || exit 1
reclaim "$WEB_PORT" WEB_PORT || exit 1

# --- dependency checks -----------------------------------------------------
if ! curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo
  echo "WARNING: Ollama is not responding on port 11434."
  echo "         Start it with:  brew services start ollama"
  echo
fi

if [ ! -x backend/.venv/bin/python ]; then
  echo "ERROR: backend/.venv is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

cleanup() {
  echo
  echo "Stopping..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo
echo "Backend  -> http://127.0.0.1:$API_PORT  (docs at /docs)"
( cd backend && ./.venv/bin/python -m uvicorn app.main:app \
    --reload --host 127.0.0.1 --port "$API_PORT" ) &

# Wait for the API before starting Vite, so the first page load isn't served
# against a backend that hasn't bound its port yet.
for _ in $(seq 1 30); do
  curl -sf --max-time 1 "http://127.0.0.1:$API_PORT/api/health/live" >/dev/null 2>&1 && break
  sleep 1
done

echo "Frontend -> http://localhost:$WEB_PORT"
( cd frontend && npm run dev -- --port "$WEB_PORT" ) &

# --- warm the models -------------------------------------------------------
# A cold Kokoro load costs ~2s and a cold Whisper load ~5s. Paying that here
# means the first real question is fast.
(
  sleep 3
  curl -sf --max-time 90 -X POST "http://127.0.0.1:$API_PORT/api/voice/synthesize" \
    -H 'Content-Type: application/json' -d '{"text":"Ready."}' -o /dev/null 2>/dev/null \
    && echo "  [warm] speech engine ready"
) &

wait
