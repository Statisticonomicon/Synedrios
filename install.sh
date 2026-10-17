#!/usr/bin/env bash
# Synedrios installer. Idempotent: safe to re-run after a pull.
#
# What it does: checks for Python 3.11+, creates .venv, installs the hash-locked runtime dependencies and the
# dev tools (pytest is needed by the self-test), creates .env from .env.example if there is none, creates the
# data directory, then runs ./selftest.sh. Nothing here needs credentials; tools/preflight.py is the check that
# does, once .env is filled in.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed. Debian/Ubuntu: sudo apt install python3 python3-venv" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required; found $(python3 --version)" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv ..."
  if ! python3 -m venv .venv; then
    echo "Could not create a virtual environment. Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  fi
fi

echo "Installing runtime dependencies (hash-locked) ..."
.venv/bin/python -m pip install --quiet --require-hashes -r requirements.txt
echo "Installing dev tools (pytest, ruff, mypy) ..."
.venv/bin/python -m pip install --quiet -r requirements-dev.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN and your LLM key before running."
fi
mkdir -p data

./selftest.sh
