# Synedrios

*From Greek συνέδριος (synedrios): one who sits together in council.*

An AI entity that joins meetings as a participant — not a note-taker, not a transcription service, but a contributor with defined roles, strengths, and rules of engagement.

## What It Does

Synedrios joins your meetings with a configurable role that determines when it speaks, what it says, and what it produces afterward. It is LLM-agnostic (works with Claude, GPT, Ollama, vLLM, or any OpenAI-compatible API) and deploys as a single Docker container.

**Phase 1 (current):** Text-based meetings via Slack.

### Pre-Built Roles

| Role | Behaviour | Post-Meeting Outputs |
|------|-----------|---------------------|
| **Secretary** | Responds when addressed. Tracks decisions and actions. | Notes, summary, action items, follow-up agenda |
| **Technical Advisor** | Speaks up on factual errors, risks, and when asked. | Notes, action items, risk register, AI task list |
| **Facilitator** | Chairs the meeting. Manages agenda and time. | Notes, summary, action items, decision log |
| **Silent Observer** | Invisible during meeting. Full outputs afterward. | Everything — notes, summary, actions, risks, decisions |
| **Coach** | Observes silently. Feedback after the session. | Notes, coaching assessment, improvement tasks |

All roles are fully customisable. You can also build your own via YAML.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Slack workspace where you can install apps
- An Anthropic API key (or any supported LLM provider)

### 1. Create a Slack App

Create it from the manifest rather than by hand — it sets every scope, event
subscription, and Socket Mode setting in one step:

1. Go to https://api.slack.com/apps → **Create New App** → **From a manifest**
2. Choose your workspace and paste `slack-app-manifest.yml` (repository root)
3. **Install to Workspace**, then collect two tokens:
   - **Bot User OAuth Token** (`xoxb-…`, OAuth & Permissions) → `SLACK_BOT_TOKEN`
   - **App-Level Token** with `connections:write` (Basic Information) → `SLACK_APP_TOKEN`

Full walkthrough, including troubleshooting: `docs/slack-setup.md`.

### 2. Configure and Run

```bash
git clone https://github.com/Statisticonomicon/Synedrios.git
cd synedrios
./install.sh          # venv, hash-locked dependencies, creates .env from .env.example, runs ./selftest.sh
# Edit .env with your tokens and API key

docker compose up -d  # or, bare: .venv/bin/python -m src
```

`install.sh` is idempotent — re-run it after every pull. `selftest.sh` proves the install without credentials
(imports, the five roles load, the schema creates, the unit tests pass); `tools.preflight` is the check that
needs your tokens.

Meeting data lives in the named Docker volume `synedrios_synedrios-data`. The
operator tools ship inside the image, so the field-test loop works in-container:

```bash
docker compose exec synedrios python -m tools.preflight
docker compose exec synedrios python -m tools.calibrate --db data/synedrios.db
```

Running bare (`python -m src`) instead writes to `./data` — keep that directory
on a disk with fast fsync, or symlink it to one; `tools.preflight` measures the
latency and fails if a meeting would lag (several SQLite commits per message).

### 3. Use It

Invite the bot to a Slack channel (`/invite @Synedrios`), check it is ready, then
start a meeting:

```bash
.venv/bin/python -m tools.preflight     # must report READY
```

```
@Synedrios start meeting "Q3 Planning" as secretary
```

Discuss normally. The bot participates according to its role. When done:

```
@Synedrios end meeting
```

Post-meeting outputs (notes, action items, summary) are posted to the channel.

### Other Commands

```
@Synedrios status          — show active meeting info
@Synedrios help            — list commands and roles
```

## Configuration

### LLM Provider

Set in `.env`. The file is loaded automatically on startup by `python -m src`
and by the operator tools (real environment variables always win); under
Docker, `env_file` in docker-compose.yml does the same job.

```bash
# Anthropic Claude (default)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-5
# Only for identity-linked Console keys (the API answers 400
# "anthropic-workspace-id is required" without it); workspace-scoped
# keys leave it unset. From console.anthropic.com/settings/workspaces:
# ANTHROPIC_WORKSPACE_ID=wrkspc_...

# OpenAI
LLM_PROVIDER=openai
ANTHROPIC_API_KEY=sk-...    # variable name will be generalised in v2
LLM_MODEL=gpt-4o

# Local Ollama
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=llama3

# VPC-internal endpoint (air-gapped)
LLM_PROVIDER=openai
LLM_BASE_URL=https://llm.internal.your-vpc.com/v1
LLM_MODEL=your-model
```

### Custom Roles

Create a YAML file in `config/modes/`:

```yaml
name: my_custom_role
description: A custom role for specific needs

initiative:
  level: 4          # 0=silent, 1=respond, 4=reactive, 6=lead
  cooldown_seconds: 30
  max_contributions_per_10min: 5
  contribution_length_words: 80
  triggers:
    - type: addressed_directly
      priority: 1
      extra:
        # false = only reply when named; true (default) = also answer open
        # questions put to the room, at initiative level 4 and above.
        # Questions aimed at a named colleague are never answered either way.
        respond_to_open_questions: true
    - type: factual_error
      priority: 2
      confidence_threshold: 0.8   # only speak when this certain (0–1)
    - type: synthesis_opportunity
      priority: 4
      extra:
        min_messages_between: 15  # refractory gap for state triggers

contributions_enabled:
  - factual_correction
  - opinion_position
  - synthesis

authority: 3        # 1=observer, 3=peer, 5=chair

tone:
  register: 2       # 1=casual, 2=professional, 3=formal
  directness: 2     # 1=blunt, 2=direct, 3=diplomatic
  warmth: 2         # 1=reserved, 2=neutral, 3=warm

post_meeting:
  outputs:
    - structured_notes
    - action_items_by_owner
```

## Testing

The Phase 1 test suite covers models, the database and audit chain, mode
configuration, the LLM adapters, the participation engine, and the Slack
connector. Unit and component tests mock every LLM and Slack call, so they make
no network requests and cost nothing.

### Set Up A Test Environment

The application targets Python 3.12 with the pinned dependencies in
`requirements.txt` (Pydantic 2). Use a virtual environment so the test stack
stays isolated from system packages:

```bash
python3 -m venv .venv
# Runtime deps are hash-locked; dev/test tools install separately (pip will not
# mix hashed and unhashed packages in one command).
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt
```

### Run The Tests

```bash
# Unit and component tests (no API calls, no Slack — fast)
.venv/bin/python -m pytest tests/ --ignore=tests/test_integration.py

# With coverage (target: 90%+ on src/engine/, 80%+ elsewhere)
.venv/bin/python -m pytest tests/ --cov=src --cov-report=term-missing

# Integration tests (real APIs — costs money, needs keys)
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest tests/test_integration.py --run-integration
```

Integration tests are skipped unless `--run-integration` is passed, and each one
skips cleanly if its API key is absent. No test reaches the network unless it is
marked `integration` and you opt in.

Deterministic meeting transcripts live in `tests/fixtures/` (standup, technical
debate, circular discussion, quick decision, long planning, direct address,
contradiction, and an empty meeting) so trigger and output behaviour is
reproducible.

## Continuous Integration & Security

Two GitHub Actions workflows ship with the project and activate automatically
if the repository is ever given a GitHub remote (this deployment runs
local-only version control, so they are dormant — run the same gates locally
with `pytest`, `ruff`, `mypy`, and `python -m pip_audit --strict`):

- **Tests** (`.github/workflows/tests.yml`) — the unit and component suite with
  coverage, plus `ruff` lint, `ruff format --check`, and `mypy`.
- **Security Audit** (`.github/workflows/security-audit.yml`) — `pip-audit`
  (known CVEs), Safety, a Trivy scan of the Docker image (fails on CRITICAL or
  HIGH), and `pip-licenses` (fails on any GPL/AGPL/LGPL/SSPL copyleft
  dependency). Also runs weekly on a schedule.

Local pre-commit hooks (`.pre-commit-config.yaml`) mirror the CI gates —
`detect-secrets`, `detect-private-key`, `ruff`, `ruff-format`, and `mypy`.
Install them once:

```bash
.venv/bin/pip install pre-commit
.venv/bin/pre-commit install
```

### Dependencies (Reproducible, Hash-Locked)

Top-level runtime dependencies are declared in `requirements.in`, including
two security floors kept green by the pip-audit CI job: `starlette>=1.3.1`
(PYSEC-2026-248/249) and `aiohttp>=3.14.3` (PYSEC-2026-3545/3546/3547). The installed
`requirements.txt` is a fully-pinned lock — every package (direct and transitive)
at an exact version with SHA-256 hashes — so builds are reproducible and
supply-chain tampering is detected. Regenerate the lock after editing
`requirements.in`:

```bash
.venv/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in
```

Development and CI tooling lives in `requirements-dev.txt` (installed separately,
not shipped in the Docker image).

## Operator Tools

Three command-line tools support running the participant against a real
workspace. None are imported by the runtime.

### Pre-Flight Check

```bash
.venv/bin/python -m tools.preflight                # every check
.venv/bin/python -m tools.preflight --no-network   # config and database only, free
```

Verifies environment variables and token formats, loads the role configs,
measures database write latency, makes one small LLM call, and tests both Slack
tokens and channel membership. Run it before the first meeting of the day.

Database write latency is the check most likely to surprise you: every incoming
message costs several SQLite commits, so a slow volume makes the bot lag behind
the conversation. Over about 500 ms per commit the check fails; the fix is to
put `./data` on a faster disk.

### Rehearsal

```bash
.venv/bin/python -m tools.rehearse --all                    # all five roles, free
.venv/bin/python -m tools.rehearse --role facilitator --verbose
.venv/bin/python -m tools.rehearse --all --live             # real LLM, real cost
.venv/bin/python -m tools.rehearse --role technical_advisor \
    --live --no-throttle                                    # calibration only
```

`--no-throttle` disables the cooldown and rate limit so every detection
surfaces and recall can be measured against a fixture's planted errors. It
never represents production behaviour — the throttles are the product.

Replays a recorded transcript from `tests/fixtures/` through the real
participation engine, the real connector, and a real database, with Slack
replaced by a recorder. It shows what the AI would have said and when, checks
that every message is recorded exactly once, and confirms the post-meeting
documents generate.

Offline is the default and costs nothing, but its detections are **synthetic** —
they exercise the trigger paths and say nothing about detection quality. Use
`--live` to judge what the AI actually says.

### Calibration

```bash
.venv/bin/python -m tools.calibrate --db data/synedrios.db
.venv/bin/python -m tools.calibrate --meeting <meeting_id>
```

Reads the append-only audit log — never writes to it — and reports, per meeting,
how often the AI spoke, what made it speak, what it suppressed and why, and
where its thresholds and cooldowns look mis-set.

## Architecture

- **Stateless workers, stateful database** — the AI identity and memory live in SQLite (PostgreSQL at scale). Workers are disposable.
- **Audit log** — every event is logged with hash chain integrity. Append-only, tamper-evident.
- **Multi-tenant from day one** — every table has `organisation_id`. Multi-tenancy is a config change, not a schema change.
- **LLM-agnostic** — adapter pattern supports any API. Swap providers without touching application code.
- **Reactive detection** — reactive roles (e.g. Technical Advisor) run LLM-based detection for factual errors and contradictions on assertive messages, gated by a cheap heuristic pre-filter so most chatter never triggers a model call. A detection only surfaces above a per-role confidence threshold; below-threshold detections are logged, not spoken, so the threshold can be tuned against real meetings. The AI never escalates its initiative to intervene.
- **Questions belong to whoever they were asked of** — a reactive role never
  answers a question put to a specific colleague, whether by @-mention or by
  opening with their name ("Sarah, can you check the migration?"). Being
  addressed by name still always reaches the AI. Slack mentions are resolved to
  display names in the transcript, so post-meeting documents read naturally and
  the engine can tell an addressed question from an open one.
- **The AI is part of its own record** — every contribution the AI makes is
  saved to the transcript like any human message, so post-meeting documents
  describe a meeting the AI was actually in, and the audit trail records every
  suppressed trigger with its type and confidence — including detector
  failures, which are logged rather than silently swallowed.
- **Turn management** — every contribution passes a cooldown and a
  ten-minute rate limit, and triggers that describe a *state* of the meeting
  (circular discussion, synthesis opportunity) also observe a refractory period
  measured in messages. Without it such a trigger, once true, stays true for the
  rest of the meeting and the AI's speaking rate ends up decided by its rate
  limiter rather than by anything that happened.
- **Meetings survive restarts** — the in-process session is a cache, not the
  source of truth. A worker that did not start a meeting rebuilds it from the
  database, including cooldown and refractory state reconstructed from the audit
  log, so a redeploy mid-meeting does not lose the meeting.
- **Cross-meeting memory** — after each meeting the avatar extracts a small set of durable, typed facts (decisions, preferences, people, relationships), deduplicated against what it already knows, and injects them into its system prompt in later meetings so it recalls prior context. Memory is stored per avatar in the database and audited on creation. A newer fact that corrects an older one demotes the old one rather than deleting it.

## File Structure

```
synedrios/
├── config/modes/          — meeting role YAML configs
│   ├── secretary.yml
│   ├── technical_advisor.yml
│   ├── facilitator.yml
│   ├── silent_observer.yml
│   └── coach.yml
├── src/
│   ├── __main__.py        — entry point
│   ├── models.py          — data models
│   ├── connectors/
│   │   └── slack.py       — Slack bot connector
│   ├── engine/
│   │   ├── modes.py       — mode config loader
│   │   └── participation.py — core participation engine
│   ├── llm/
│   │   └── adapter.py     — LLM adapter layer
│   └── persistence/
│       └── database.py    — SQLite database layer
├── install.sh             — idempotent install: venv, hash-locked deps, .env from the example, self-test
├── selftest.sh            — proves the install without credentials; names what failed
├── tools/                 — operator tooling (not imported by the runtime)
│   ├── preflight.py       — credential, config, and latency checks
│   ├── rehearse.py        — replay a transcript through the full stack
│   ├── calibrate.py       — audit-log analysis and threshold tuning
│   ├── scripted_llm.py    — deterministic offline LLM for rehearsals
│   └── build_public.sh    — builds the sanitised public copy from HEAD (maintainer only)
├── release/               — sources only the public build uses (the public HANDOVER.md)
├── docs/                  — design documents and setup guides
│   ├── slack-setup.md     — connecting to a Slack workspace
│   └── legal/             — Synedrios-NDA.docx (one-page one-way NDA template, English law);
│       └── patent/        — GB patent application description (.md source, .docx, A4 .pdf) and filing checklist
├── slack-app-manifest.yml — Slack app definition (scopes, events, Socket Mode)
├── data/                  — persistent data (Docker volume)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── SECURITY.md
├── LICENSE                — Business Source License 1.1
└── README.md
```

## Data Flow

Synedrios receives **nothing**. No telemetry, no phone-home, no usage reporting. All data stays in the customer's infrastructure.

The only route where data exits the customer's perimeter is the LLM API call — using the customer's own API key and agreement with their LLM provider. This is entirely avoidable by using an internal LLM endpoint.

## Licence

Business Source License 1.1. Free for internal production use. Commercial licence required for offering as a hosted service to third parties. Each version converts to Apache 2.0 four years after release.

See [LICENSE](LICENSE) for full terms.

## Roadmap

- **v1 (current):** Slack text-based meetings. 5 roles. SQLite. Claude API.
- **v2:** Voice meetings via Recall.ai (Zoom/Teams/Meet). PostgreSQL. RBAC. Multiple LLM adapters. Additional roles.
- **v3:** Full voice participation (TTS). Semantic memory search. Enterprise features.
- **v4:** Cloud marketplace listings (AWS, Azure, GCP).

## Contributing

See SECURITY.md for vulnerability reporting. Contributions welcome via pull request.
