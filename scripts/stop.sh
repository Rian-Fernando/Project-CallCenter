#!/usr/bin/env bash
# Stop everything this project starts. Use before closing the laptop, or when
# something is wedged and you want a known-clean state.
set -uo pipefail

echo "Stopping Garden City services..."

for port in 8000 5173; do
  pids="$(lsof -ti:$port 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null; sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    echo "  stopped :$port"
  fi
done

pkill -f 'uvicorn app.main:app' 2>/dev/null && echo "  stopped backend" || true
pkill -f 'node_modules/.bin/vite' 2>/dev/null && echo "  stopped frontend" || true

if [ "${1:-}" = "--all" ]; then
  pkill -f 'cloudflared tunnel' 2>/dev/null && echo "  stopped tunnel" || true
  echo "  (Ollama left running — stop it with: brew services stop ollama)"
fi

sleep 1
echo
for port in 8000 5173; do
  lsof -ti:$port >/dev/null 2>&1 && echo "  :$port STILL BUSY" || echo "  :$port free"
done
