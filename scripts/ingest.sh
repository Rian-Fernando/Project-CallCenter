#!/usr/bin/env bash
# Rebuild the knowledge base index from crawled pages and local files.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec ./backend/.venv/bin/python scripts/ingest.py "$@"
