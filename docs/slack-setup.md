# Connecting Synedrios To A Slack Workspace

Create the Slack app from the manifest, copy two tokens into `.env`, invite the
bot to a channel, run the pre-flight check, then start a meeting. Roughly
fifteen minutes end to end, most of it waiting for Slack's UI.

This is the human half of task **T6**. The software half — rehearsals against
recorded transcripts, threshold calibration — needs no workspace and is
described in `docs/t6-field-test.md`.

---

## 1. Create The App

1. Go to <https://api.slack.com/apps> → **Create New App** → **From a manifest**.
2. Choose the workspace. Use a test workspace first if the real one is busy.
3. Paste the contents of `slack-app-manifest.yml` (repository root). Review the
   scopes it requests — they are annotated in the file, one comment per scope.
4. **Create**, then **Install to Workspace** and approve.

## 2. Collect The Two Tokens

Synedrios needs both. They do different jobs and are not interchangeable.

| Token | Where to find it | Starts with | What it does |
|---|---|---|---|
| Bot token | OAuth & Permissions → Bot User OAuth Token | `xoxb-` | Calls the Web API: posting messages, reading profiles |
| App-level token | Basic Information → App-Level Tokens → Generate, scope `connections:write` | `xapp-` | Opens the Socket Mode WebSocket |

Copy `.env.example` to `.env` and fill in:

```
SLACK_BOT_TOKEN=xoxb-…
SLACK_APP_TOKEN=xapp-…
ANTHROPIC_API_KEY=sk-ant-…
```

Edit the file with `sudo xed .env`. It is git-ignored; keep it that way.

## 3. Invite The Bot To A Channel

In the channel you will meet in:

```
/invite @Synedrios
```

The bot only receives messages from channels it belongs to. This is the whole
of its access boundary — it cannot read a channel it has not been invited to.

## 4. Run The Pre-Flight Check

```bash
.venv/bin/python -m tools.preflight
```

It verifies the environment variables, loads the five role configs, measures
database write latency, makes one small LLM call, and tests both Slack tokens
and channel membership. Fix anything marked `FAIL` before meeting.

The one that catches people out is **database write latency**. Every incoming
message costs several SQLite commits, so a slow volume makes the bot lag behind
the conversation by seconds per message. Over about 500 ms per commit the check
fails and the fix is to move `./data` onto a faster disk — on the development
machine, the project volume measures around 5,000 ms per commit and `/tmp`
around 3 ms.

## 5. Run A Meeting

```bash
.venv/bin/python -m src          # or: docker compose up -d
```

In the channel:

```
@Synedrios start meeting "Q3 Roadmap Review" as technical_advisor
… hold the meeting …
@Synedrios end meeting
```

Other commands: `@Synedrios status`, `@Synedrios help`.

Available roles: `secretary`, `technical_advisor`, `facilitator`,
`silent_observer`, `coach`.

## 6. Review What Happened

```bash
.venv/bin/python -m tools.calibrate --db data/synedrios.db
```

Per meeting: how often the AI spoke, what made it speak, what it nearly said
and suppressed, and where its thresholds and cooldowns look wrong. Record the
findings against the observation sheet in `docs/t6-field-test.md`.

---

## Troubleshooting

**The bot never responds.** Check it is in the channel (`/invite`), that the
process is running, and that `tools.preflight` passes. Socket Mode failures
appear in the process log immediately on startup.

**"missing_scope" in the log.** The manifest was edited, or the app was
installed before a scope was added. Add the scope under OAuth & Permissions,
then **reinstall** the app — new scopes need a fresh install.

**Speakers appear as `U08KX3L4Q2`.** The `users:read` scope is missing.
Add it and reinstall. The transcript is what every post-meeting document is
written from, so unresolved ids propagate into all of them.

**The AI answers, but twice.** Should not happen: a message that mentions the
bot arrives as both `app_mention` and `message`, and the connector drops the
second copy. If it recurs, check that `_resolve_bot_user_id` is finding the
bot's user id — without it the de-duplication guard cannot engage.

**The AI speaks too much or too little.** That is what T6 is for. Do not edit
the engine; adjust the role's `cooldown_seconds`, `max_contributions_per_10min`,
`confidence_threshold`, and `min_messages_between` in `config/modes/*.yml`, and
record what you changed and why.

**Everything lags.** Check database write latency (see step 4) and the LLM
round-trip time reported by the pre-flight check.
