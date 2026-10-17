"""Pre-flight check before a live meeting (task T6).

Verifies, in order, everything that has to be true before the bot is useful in
a real Slack workspace: configuration, role definitions, database write speed,
LLM reachability, Slack authentication, and which channels the bot can actually
see. Run it before the first meeting of the day; it is much cheaper to find a
missing scope here than in front of colleagues.

    python -m tools.preflight              # every check
    python -m tools.preflight --no-network # config and database only, free

Network checks make one small LLM call and a handful of Slack API calls.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from pathlib import Path
from typing import Any, Optional

from src.engine.modes import load_all_modes
from src.envfile import load_env_file
from src.llm.adapter import create_adapter
from src.models import AuditEntry, AuditEventType
from src.persistence.database import Database

# A meeting message costs several database commits (transcript row, audit
# entries, transcript reads). Past roughly 100 ms per commit the bot starts to
# lag noticeably behind the conversation; past 500 ms it cannot keep up at all.
COMMIT_WARN_MS = 100.0
COMMIT_FAIL_MS = 500.0

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


class Report:
    """Accumulates check results and decides the exit status."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.rows.append((status, check, detail))
        print(f"[{status}] {check}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, _, _ in self.rows)

    def summary(self) -> str:
        counts = {s: sum(1 for r in self.rows if r[0] == s) for s in (PASS, WARN, FAIL)}
        verdict = "NOT READY" if self.failed else "READY"
        return (
            f"\n{counts[PASS]} passed, {counts[WARN]} warning(s), "
            f"{counts[FAIL]} failure(s) — {verdict}."
        )


def check_environment(report: Report) -> None:
    """Required and optional environment variables."""
    if Path(".env").exists():
        report.add(PASS, ".env present")
    else:
        report.add(WARN, ".env missing", "copy .env.example to .env and fill it in")

    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    expected = {
        "SLACK_BOT_TOKEN": "xoxb-",
        "SLACK_APP_TOKEN": "xapp-",
    }
    if provider == "anthropic":
        expected["ANTHROPIC_API_KEY"] = "sk-ant-"

    for name, prefix in expected.items():
        value = os.environ.get(name, "")
        if not value:
            report.add(FAIL, f"{name} set", "missing from the environment")
        elif not value.startswith(prefix):
            report.add(
                WARN, f"{name} set", f"does not start with `{prefix}` — is it the right token?"
            )
        else:
            report.add(PASS, f"{name} set", f"{value[: len(prefix) + 4]}…")

    report.add(
        PASS,
        "LLM configuration",
        f"provider={provider}, model={os.environ.get('LLM_MODEL', '(adapter default)')}",
    )


def check_modes(report: Report) -> None:
    """Role definitions load and are internally sensible."""
    modes = load_all_modes("config/modes")
    if not modes:
        report.add(FAIL, "role configs", "none loaded from config/modes/")
        return
    report.add(PASS, "role configs", f"{len(modes)} loaded: {', '.join(sorted(modes))}")

    for name, mode in sorted(modes.items()):
        level = mode.initiative.level
        triggers = len(mode.initiative.triggers)
        if level > 0 and triggers == 0:
            report.add(
                WARN,
                f"role `{name}`",
                f"initiative level {level} but no triggers — it will never speak",
            )
        elif level == 0 and triggers:
            report.add(
                WARN,
                f"role `{name}`",
                "silent role with triggers configured — the triggers are dead weight",
            )


def check_database(report: Report, db_path: str) -> None:
    """The database is writable, and the volume is fast enough for a meeting."""
    try:
        db = Database(db_path)
    except Exception as exc:
        report.add(FAIL, "database opens", f"{type(exc).__name__}: {exc}")
        return
    report.add(PASS, "database opens", str(db.db_path))

    # Measured on a scratch database beside the real one: same volume, same
    # SQLite settings, but the audit log stays a record of meetings rather than
    # of diagnostics.
    probe_path = db.db_path.with_suffix(db.db_path.suffix + ".preflight")
    samples: list[float] = []
    try:
        probe = Database(probe_path)
        for i in range(5):
            start = time.perf_counter()
            probe.append_audit(
                AuditEntry(
                    meeting_id="preflight",
                    event_type=AuditEventType.CONFIG_CHANGE,
                    payload={"check": "write latency", "n": i},
                )
            )
            samples.append((time.perf_counter() - start) * 1000)
    except Exception as exc:
        report.add(FAIL, "database writes", f"{type(exc).__name__}: {exc}")
        return
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(probe_path) + suffix).unlink(missing_ok=True)

    median = statistics.median(samples)
    detail = f"median {median:.0f} ms per commit (of {len(samples)})"
    if median >= COMMIT_FAIL_MS:
        report.add(
            FAIL,
            "database write latency",
            f"{detail} — a single message costs several commits, so the bot "
            f"will fall badly behind the conversation. Move the database to a "
            f"faster volume (the ./data mount in docker-compose.yml).",
        )
    elif median >= COMMIT_WARN_MS:
        report.add(WARN, "database write latency", f"{detail} — noticeably slow; watch for lag.")
    else:
        report.add(PASS, "database write latency", detail)


async def check_llm(report: Report) -> None:
    """The configured LLM answers a trivial prompt."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if provider == "anthropic" and not api_key:
        report.add(FAIL, "LLM reachable", "no ANTHROPIC_API_KEY to test with")
        return

    adapter = create_adapter(
        {
            "provider": provider,
            "api_key": api_key,
            "model": os.environ.get("LLM_MODEL", ""),
            "base_url": os.environ.get("LLM_BASE_URL", ""),
            "workspace_id": os.environ.get("ANTHROPIC_WORKSPACE_ID", ""),
        }
    )
    try:
        start = time.perf_counter()
        reply = await adapter.complete(
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            system="You are a connectivity check. Answer in one word.",
            max_tokens=64,  # headroom for adaptive thinking on current models
            temperature=0.0,
        )
        elapsed = (time.perf_counter() - start) * 1000
        report.add(
            PASS, "LLM reachable", f"{adapter.name}, {elapsed:.0f} ms, replied {reply[:40]!r}"
        )
    except Exception as exc:
        report.add(FAIL, "LLM reachable", f"{type(exc).__name__}: {exc}")
    finally:
        close = getattr(adapter, "close", None)
        if close:
            await close()


async def check_slack(report: Report) -> None:
    """Both Slack tokens work, and the bot can see at least one channel."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not bot_token:
        report.add(FAIL, "Slack bot token", "not set — cannot test")
        return

    from slack_sdk.web.async_client import AsyncWebClient

    client = AsyncWebClient(token=bot_token)
    bot_user_id = ""
    try:
        auth = await client.auth_test()
        bot_user_id = auth.get("user_id", "")
        report.add(
            PASS,
            "Slack bot token",
            f"workspace `{auth.get('team')}`, bot `{auth.get('user')}` ({bot_user_id})",
        )
    except Exception as exc:
        report.add(FAIL, "Slack bot token", f"{type(exc).__name__}: {exc}")
        return

    try:
        convos = await client.users_conversations(
            user=bot_user_id, types="public_channel,private_channel", limit=50
        )
        channels: list[dict[str, Any]] = convos.get("channels", []) or []
        names = [f"#{c.get('name', '?')}" for c in channels]
        if names:
            report.add(PASS, "bot channel membership", ", ".join(names[:10]))
        else:
            report.add(
                WARN,
                "bot channel membership",
                "the bot is in no channels — invite it with /invite @Synedrios",
            )
    except Exception as exc:
        report.add(WARN, "bot channel membership", f"could not list: {exc}")

    if not app_token:
        report.add(FAIL, "Slack app token", "not set — Socket Mode cannot start")
        return
    try:
        # apps.connections.open authenticates with the app-level token passed
        # as an argument, not as the client's bearer token.
        opened = await AsyncWebClient().apps_connections_open(app_token=app_token)
        report.add(
            PASS,
            "Slack app token",
            "Socket Mode connection granted" if opened.get("url") else "granted, no URL returned",
        )
    except Exception as exc:
        report.add(
            FAIL,
            "Slack app token",
            f"{type(exc).__name__}: {exc} — check the token has connections:write",
        )


async def run(args: argparse.Namespace) -> int:
    print("Synedrios Pre-Flight Check")
    print("=" * 60)

    report = Report()
    check_environment(report)
    check_modes(report)
    check_database(report, args.db)

    if args.no_network:
        report.add(WARN, "network checks", "skipped (--no-network)")
    else:
        await check_llm(report)
        await check_slack(report)

    print(report.summary())
    return 1 if report.failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check Synedrios is ready for a live meeting.")
    parser.add_argument("--db", default="data/synedrios.db", help="Database path to test.")
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Skip the LLM and Slack checks (no API calls, no cost).",
    )
    args = parser.parse_args(argv)
    load_env_file()  # bare-metal runs read .env themselves; real env wins
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
