#!/usr/bin/env bash
# Synedrios self-test: proves an install works, without credentials or network.
#
# Checks: the virtual environment exists; the engine, connector and adapter import; the five role configs
# load; the database schema creates in a temporary file; the unit tests pass. It names what failed.
# For the credentialed check (Slack, LLM, database latency) run: .venv/bin/python -m tools.preflight
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
rc=0
say() { echo "$*"; }

[ -x "$PY" ] || { say "FAIL  .venv missing — run ./install.sh"; exit 1; }

if $PY -c "import src.engine.participation, src.connectors.slack, src.llm.adapter, src.persistence.database" 2>/tmp/synedrios-selftest.err; then
  say "ok    imports"
else
  say "FAIL  imports:"; sed 's/^/        /' /tmp/synedrios-selftest.err; rc=1
fi

if out=$($PY -c "
from src.engine.modes import load_all_modes
m = load_all_modes('config/modes')
assert len(m) == 5, sorted(m)
print(', '.join(sorted(m)))" 2>&1); then
  say "ok    five roles load: $out"
else
  say "FAIL  role configs: $out"; rc=1
fi

tmpdb=$(mktemp -u /tmp/synedrios-selftest.XXXXXX.db)
if out=$($PY -c "
from src.persistence.database import Database
Database('$tmpdb')
print('schema created')" 2>&1); then
  say "ok    database $out"
else
  say "FAIL  database: $out"; rc=1
fi
rm -f "$tmpdb" "$tmpdb-wal" "$tmpdb-shm"

if $PY -m pytest tests/ --ignore=tests/test_integration.py -q -p no:cacheprovider >/tmp/synedrios-selftest.log 2>&1; then
  say "ok    unit tests: $(tail -1 /tmp/synedrios-selftest.log)"
else
  say "FAIL  unit tests; last lines:"; tail -15 /tmp/synedrios-selftest.log | sed 's/^/        /'; rc=1
fi

[ -f .env ] && say "ok    .env present (fill it in, then: $PY -m tools.preflight)" || say "note  no .env yet — ./install.sh creates it"

[ $rc -eq 0 ] && say "SELFTEST PASSED" || say "SELFTEST FAILED"
exit $rc
