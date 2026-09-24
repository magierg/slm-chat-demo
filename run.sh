#!/usr/bin/env bash
# Launcher that never needs the venv activated. Creates/installs it on first run.
#
#   ./run.sh --all
#   ./run.sh --raw
#   ./run.sh --mcp --memory
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Setting up .venv (first run)..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec .venv/bin/python chat.py "$@"
