# Synedrios — User Guide

## Part 1 — What this does and why you want it

Synedrios sits in your Slack meeting as a participant with a job — secretary,
technical advisor, facilitator, observer or coach — speaks when its role says
to, and posts the notes, actions and decisions to the channel the moment the
meeting ends. You want it if your meetings lose their decisions between one
week and the next.

---

## Part 2 — Getting started

Four steps to a first meeting. `docs/slack-setup.md` has every screen.

**Before you start:** Docker with the Compose plugin; a Slack workspace where
you can install apps; an Anthropic API key (or any supported provider).

**1. Create the Slack app from the manifest.** <https://api.slack.com/apps> →
*Create New App* → *From a manifest* → paste `slack-app-manifest.yml` →
*Install to Workspace*. Collect the **Bot User OAuth Token** (`xoxb-…`) and an
**App-Level Token** with `connections:write` (`xapp-…`).

**2. Configure.**
```bash
cd synedrios                                 # the folder you cloned into
cp -n .env.example .env && $EDITOR .env      # SLACK_BOT_TOKEN, SLACK_APP_TOKEN, your LLM key
```

**3. Start and check.**
```bash
docker compose up -d
docker compose exec synedrios python -m tools.preflight     # expected: READY
```

**4. Run a meeting.** `/invite @Synedrios` in a channel, then:
```
@Synedrios start meeting "Q3 Planning" as secretary
… discuss …
@Synedrios end meeting
```
**Expected:** the bot acknowledges the start, behaves per its role, and posts
notes, actions and a summary at the end.

### If something went wrong

Part 3, "Troubleshooting", and `docs/slack-setup.md` §Troubleshooting. The
ones that catch everyone: a token pasted into the wrong variable, and a bot
that was never invited to the channel.

---

## Part 3 — The full guide

### Roles

| Role | During the meeting | Afterwards |
|---|---|---|
| Secretary | responds when addressed; tracks decisions and actions | notes, summary, actions, follow-up agenda |
| Technical Advisor | speaks on factual errors, risks, and when asked | notes, actions, risk register, AI task list |
| Facilitator | chairs: agenda and time | notes, summary, actions, decision log |
| Silent Observer | invisible | everything |
| Coach | observes | notes, coaching assessment, improvement tasks |

`@Synedrios help` lists roles; `@Synedrios status` shows the active meeting.
Roles are YAML under `config/`; copy one to make your own.

### Configuration

`.env` holds the tokens, the LLM provider and model, and storage paths;
`README.md` § Configuration documents every key. Meeting data lives in the
named volume `synedrios_synedrios-data` (Docker) or `./data` (bare). **The
pre-flight check measures storage fsync latency and refuses to run a meeting
that would lag** — several SQLite commits per message. If it fails, put
`./data` on a fast disk.

### Operator tools

```bash
docker compose exec synedrios python -m tools.preflight                 # READY, or what is wrong
docker compose exec synedrios python -m tools.calibrate --db data/synedrios.db   # review a finished meeting
```
`README.md` § Operator Tools has the rest. The field-test protocol and
scenarios are in `docs/t6-field-test.md`.

### Verifying it works

Pre-flight `READY`; `docker compose logs -f synedrios` shows Socket Mode
connected; a `start meeting` gets a reply. Tests, from a dev checkout:
`.venv/bin/python -m pytest` (the current count is in `HANDOVER.md`).

### Troubleshooting

**Symptom:** the bot does not answer `@Synedrios`.
**Cause:** not invited to the channel, or Socket Mode not connected.
**Fix:** `/invite @Synedrios`; check `SLACK_APP_TOKEN` has `connections:write`;
read the logs.

**Symptom:** `preflight` reports storage too slow.
**Cause:** `./data` on a slow or network filesystem.
**Fix:** move or symlink it to a disk with fast fsync.

**Symptom:** LLM errors at meeting start.
**Cause:** wrong key or provider/model pair in `.env`.
**Fix:** `README.md` § Configuration; for Ollama/vLLM, the base URL must be
reachable from inside the container.

More: `docs/slack-setup.md` § Troubleshooting.

### Networking — with and without Tailscale

Synedrios connects *outbound* to Slack over Socket Mode and to your LLM
provider; it listens on no port. Without a tailnet, nothing to do. With
Tailscale: only relevant if your LLM runs on another tailnet machine — point
the provider URL at its tailnet address. Node identity is not backed up.
