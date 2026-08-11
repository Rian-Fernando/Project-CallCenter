#!/usr/bin/env bash
# Run the test suite. Integration tests self-skip if Ollama or the KB is absent.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"
exec ./.venv/bin/python -m pytest "$@"
