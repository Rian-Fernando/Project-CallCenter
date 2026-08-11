#!/usr/bin/env bash
# Politely crawl official Village pages into the local cache.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec ./backend/.venv/bin/python scripts/crawl.py "$@"
