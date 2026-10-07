#!/usr/bin/env bash
# One-command start: creates .venv on first run, installs requirements, starts the
# mock vendor-risk API (port 8001) and the UI (port 8501).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Add GOOGLE_API_KEY (or set LLM_MODE=replay / off)."; }
exec .venv/bin/python run_local.py
