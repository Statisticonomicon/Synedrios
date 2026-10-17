# Handover — Synedrios

For a maintainer or an AI session picking this project up. Current state, how to verify it, what is open, and
what silently breaks. Design and the record of decisions (D1–D18) are in `docs/ARCHITECTURE.md`; how to deploy
and use it is `docs/USER_GUIDE.md`.

## Current State

- **Phase 1 is built and tested.** The participation engine (`src/engine/participation.py`) with heuristic and
  LLM-detected triggers (`factual_error`, `contradiction`), the turn-taking gate (cooldown, per-window budget,
  per-trigger refractory period), the five built-in roles (`config/modes/`), the Slack connector (Socket Mode),
  the hash-chained append-only audit log, post-meeting outputs and cross-meeting memory extraction.
- **Operator tooling:** `tools/preflight.py` (credentials, configs, database latency, LLM, Slack),
  `tools/rehearse.py` (replay a transcript fixture through the full stack, offline with `tools/scripted_llm.py`
  or `--live`), `tools/calibrate.py` (audit-log analysis and threshold tuning).
- **Tests:** unit and integration suites under `tests/` with eight transcript fixtures. `pytest --collect-only -q`
  is the count. The integration tests need real keys and spend money; they are opt-in (`--run-integration`).
- **Not yet done:** a sustained field test in a real workspace with real meetings. Rehearsals against recorded
  transcripts have run for all five roles; live use has been limited to short runs.

## How To Verify The Project Is Healthy

```bash
./install.sh                      # idempotent; ends by running ./selftest.sh
./selftest.sh                     # imports, the five roles load, schema creates, unit tests pass — no credentials
.venv/bin/python -m tools.preflight   # with .env filled in: expected READY
.venv/bin/python -m pytest tests/ --ignore=tests/test_integration.py
.venv/bin/python -m tools.rehearse --all   # offline replay of every fixture
```

## Open Work

1. **Field test.** Five real meetings, one per role, silent roles first, per `docs/t6-field-test.md`. Run
   `tools.preflight` before each and `tools.calibrate` after each. The decisions marked provisional in
   `docs/ARCHITECTURE.md` (D12–D14) are confirmed or overridden by what the audit log shows.
2. **State triggers carry little signal.** `circular_discussion` and `synthesis_opportunity` are word-overlap and
   speaker-count heuristics that fire on most multi-person meetings; the refractory gate (D12) limits the damage.
   Making them LLM-detected like `factual_error` is the likely fix. See `docs/ARCHITECTURE.md` D17.
3. **Designed, not implemented:** side-channel alerts (`docs/participation-model.md` Part 2, D9), custom-role
   inheritance (`inherits:` in the custom-role schema), explicit initiative-level enforcement in the turn gate
   (today a level-0 role is silent because its contribution budget is 0), an audit entry for events at which no
   trigger fired, a database-level constraint making the audit table append-only (today it is append-only by
   application convention), and a production chain verifier (the chain is verified in tests only).
4. **Later versions:** a web GUI (v1.1), Zoom/Teams/Meet connectors, multi-tenant configuration beyond the
   single default organisation (every table already carries `organisation_id`).

## Key Names And Terms

- **Role** — a preset over six dimensions: initiative (0–6), contribution types, attention areas, authority
  (1–5), tone (five dials), post-meeting outputs. `config/modes/*.yml`.
- **Trigger** — a detector producing `{trigger_type, fired, confidence, priority, reason}`; heuristic or
  LLM-assisted.
- **Turn gate** — `can_contribute`: cooldown, budget per 600 s, and the per-trigger refractory period.
- **Audit log** — `audit_log` table; SHA-256 chain per meeting; entry types in `src/models.py`
  (`AuditEventType`), including `trigger_suppressed` and `contribution_withheld`.
- **Adapter** — `src/llm/adapter.py`; every LLM call goes through it (Anthropic, OpenAI-compatible endpoints,
  local servers such as Ollama or vLLM).
- **Fixtures** — `tests/fixtures/*.json`, replayed by `tools/rehearse.py`.

## Gotchas

- **Never point the SQLite database at a slow or network-mounted disk.** Each audit append fsyncs; on a disk
  that fsyncs in seconds the engine falls behind the meeting. Keep `data/` on local storage.
- **Runtime and dev dependencies install in two commands.** `requirements.txt` is hash-locked and pip refuses to
  mix hashed and unhashed packages in one install; `install.sh` does both in order.
- **Run tools as `.venv/bin/python -m …`**, not through the console scripts in `.venv/bin`, whose shebangs
  embed the folder path and break when the folder is moved or renamed.
- **Integration tests and `--live` rehearsals spend real API money.**
- **The AI never raises its own initiative level during a meeting**, whatever happens. That is a design rule
  (`docs/participation-model.md`), not a bug; urgent conditions are for the side-channel alert path.
- **CI workflows** under `.github/workflows/` run tests, lint, types and `pip-audit` on push.
