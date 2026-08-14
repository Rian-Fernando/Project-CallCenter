#!/usr/bin/env bash
# Pre-demo check. Run this 15 minutes before presenting.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; echo "      → $2"; fail=$((fail+1)); }

echo "=== Garden City — pre-demo check ==="
echo

# Memory: local inference is the first thing to suffer under pressure.
free_gb=$(vm_stat | awk '/free/{f=$3} /inactive/{i=$3} END {gsub(/\./,"",f); gsub(/\./,"",i); printf "%.1f", (f+i)*16384/1073741824}')
if (( $(echo "$free_gb >= 4.0" | bc -l) )); then ok "RAM available: ${free_gb} GB"
else bad "Only ${free_gb} GB RAM free" "Quit VS Code, Slack, Docker, extra Chrome windows"; fi

# Power: macOS throttles the CPU hard on battery.
if pmset -g ps 2>/dev/null | grep -q 'AC Power'; then ok "On AC power"
else bad "Running on battery" "Plug in — macOS throttles CPU and answers get slow"; fi

# CPU headroom
top_cpu=$(ps aux | awk 'NR>1 {s+=$3} END {printf "%.0f", s}')
if [ "$top_cpu" -lt 200 ]; then ok "CPU load reasonable (${top_cpu}%)"
else bad "High CPU load (${top_cpu}%)" "Something heavy is running — quit it"; fi

curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1 \
  && ok "Ollama running" || bad "Ollama not responding" "brew services start ollama"

health=$(curl -sf --max-time 60 http://127.0.0.1:8000/api/health 2>/dev/null || echo "")
if [ -n "$health" ]; then
  ready=$(echo "$health" | python3 -c "import json,sys;print(json.load(sys.stdin)['ready_for_calls'])" 2>/dev/null)
  [ "$ready" = "True" ] && ok "Backend ready for calls" || bad "Backend not ready" "Check /api/health"
  chunks=$(echo "$health" | python3 -c "import json,sys;print(json.load(sys.stdin)['services']['vector_store']['meta'].get('chunks',0))" 2>/dev/null)
  [ "${chunks:-0}" -gt 0 ] && ok "Knowledge base: $chunks chunks" || bad "Knowledge base empty" "./scripts/ingest.sh"
else
  bad "Backend not running" "./scripts/dev.sh"
fi

curl -sf --max-time 5 http://localhost:5173/ >/dev/null 2>&1 \
  && ok "Frontend serving on :5173" || bad "Frontend not running" "./scripts/dev.sh"

if curl -sf --max-time 15 https://gardencity-api.rianfernando.com/api/health/live >/dev/null 2>&1; then
  ok "Public tunnel reachable"
else
  echo "  – Public tunnel down (only needed for the Vercel URL)"
  echo "      → ./scripts/tunnel.sh run"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "All $pass checks passed. Warming models..."
  curl -sf --max-time 90 -X POST http://127.0.0.1:8000/api/voice/synthesize \
    -H 'Content-Type: application/json' -d '{"text":"Ready for the demo."}' -o /dev/null 2>/dev/null
  curl -sf --max-time 120 -X POST http://127.0.0.1:8000/api/chat \
    -H 'Content-Type: application/json' -d '{"message":"When is garbage collection?"}' >/dev/null 2>&1
  echo "Warm. You're good to go."
else
  echo "$fail check(s) failed — fix the arrows above."
  exit 1
fi
