#!/usr/bin/env bash
# One-command start: creates .venv on first run, installs requirements, starts the
# mock vendor-risk API (port 8001) and the UI (port 8501).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.13 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.11+ is required (set PYTHON=/path/to/python3.11)."; exit 1; }
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Add GOOGLE_API_KEY (or set LLM_MODE=replay / off)."; }
exec .venv/bin/python run_local.py
