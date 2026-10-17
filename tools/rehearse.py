"""Rehearse a meeting against the full participation stack (task T6).

Replays a transcript fixture through the real `SlackConnector`, the real
`ParticipationEngine`, and the real database — everything the live bot runs
except the Slack socket itself. Slack is replaced by a recorder that captures
what the AI would have posted.

This is not T6. T6 is a real meeting with real people, and only that can judge
whether the AI says useful things at useful moments. What a rehearsal does is
remove the failure modes you should never discover in front of colleagues: a
crash, a duplicate message, a role that speaks forty times, a post-meeting
document that never generates.

Usage:
    python -m tools.rehearse --all                       # every role, offline, free
    python -m tools.rehearse --role secretary
    python -m tools.rehearse --role technical_advisor --fixture technical_debate
    python -m tools.rehearse --all --live                # real LLM, real cost
    python -m tools.rehearse --all --out rehearsal.md

Offline is the default: no API key, no network, no cost. Offline detections are
synthetic (see `tools.scripted_llm`) — they prove the code path runs, not that
the detector is any good. Use `--live` to judge what the AI actually says.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.connectors import slack as slack_module
from src.connectors.slack import SlackConnector
from src.engine import participation as participation_module
from src.engine.modes import load_all_modes
from src.envfile import load_env_file
from src.llm.adapter import LLMAdapter, create_adapter
from src.models import EventType, MeetingEvent
from src.persistence.database import Database
from tools.calibrate import build_rows, format_report
from tools.scripted_llm import ScriptedLLMAdapter

FIXTURE_DIR = Path("tests/fixtures")
BOT_USER_ID = "U0REHEARSAL"

# Which fixture best exercises each role. A Secretary is judged on its notes, so
# a dense standup suits it; a Technical Advisor is judged on whether it corrects
# real errors without nagging, so it gets the debate with planted errors.
DEFAULT_FIXTURES: dict[str, str] = {
    "secretary": "simple_standup",
    "technical_advisor": "technical_debate",
    "facilitator": "circular_meeting",
    "silent_observer": "long_planning",
    "coach": "technical_debate",
}


class SimulatedClock:
    """A stand-in for `time` that advances only when the rehearsal says so.

    Turn management is time-based: cooldowns in seconds, a rate limit over a
    ten-minute window. Replaying a 40-message meeting in two seconds of real
    time would let a single cooldown swallow the whole meeting and tell you
    nothing. This gives the replay a plausible meeting clock instead.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class RecordingSay:
    """Stands in for Slack's `say`, capturing what the AI would have posted."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._sink: Optional[list[str]] = None

    async def __call__(self, text: str) -> None:
        self.messages.append(text)
        if self._sink is not None:
            self._sink.append(text)

    def capture(self) -> list[str]:
        """Start capturing into a fresh list and return it."""
        self._sink = []
        return self._sink

    def stop(self) -> None:
        self._sink = None


@dataclass
class RehearsalResult:
    """What one role's rehearsal produced."""

    role: str
    fixture: str
    meeting_id: str = ""
    timeline: list[str] = field(default_factory=list)
    intro: str = ""
    post_meeting: list[str] = field(default_factory=list)
    duplicate_events: int = 0
    replayed: int = 0
    crashed: str = ""


def load_fixture(name: str) -> list[MeetingEvent]:
    """Load a transcript fixture by name (without the .json extension)."""
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in FIXTURE_DIR.glob("*.json")))
        raise FileNotFoundError(f"No fixture `{name}`. Available: {available}")
    raw = json.loads(path.read_text())
    return [
        MeetingEvent(
            event_type=EventType(item.get("event_type", "chat")),
            speaker=item["speaker"],
            content=item["content"],
            confidence=item.get("confidence", 1.0),
        )
        for item in raw
    ]


def _slack_user_id(speaker: str) -> str:
    """A stable fake Slack user id for a fixture speaker."""
    return "U" + re.sub(r"[^A-Z0-9]", "", speaker.upper())[:8].ljust(8, "0")


def _addresses_bot(content: str, participant_name: str) -> bool:
    """Would this fixture line have been an @-mention of the bot in Slack?"""
    return participant_name.lower() in content.lower()


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


async def rehearse_role(
    role: str,
    fixture_name: str,
    db: Database,
    llm: LLMAdapter,
    modes: dict[str, Any],
    participant_name: str,
    pace_seconds: float,
    no_throttle: bool = False,
) -> RehearsalResult:
    """Replay one fixture through one role, end to end.

    no_throttle=True removes the cooldown and rate limit for CALIBRATION ONLY:
    it lets every detection surface so recall can be measured against planted
    errors. It is never how the product runs — the throttles are the product.
    """
    result = RehearsalResult(role=role, fixture=fixture_name)
    if no_throttle:
        modes = dict(modes)
        unthrottled = modes[role].model_copy(deep=True)
        unthrottled.initiative.cooldown_seconds = 0
        unthrottled.initiative.max_contributions_per_10min = 10_000
        modes[role] = unthrottled
    events = load_fixture(fixture_name)
    result.replayed = len(events)

    clock = SimulatedClock()
    participation_module.time = clock  # type: ignore[assignment]
    slack_module.time = clock  # type: ignore[assignment]

    say = RecordingSay()
    connector = SlackConnector(db=db, llm=llm, modes=modes, participant_name=participant_name)
    connector._bot_user_id = BOT_USER_ID
    channel = f"C-REHEARSE-{role.upper()}"

    def mention_event(text: str, speaker: str = "Nikos") -> dict[str, Any]:
        return {
            "text": f"<@{BOT_USER_ID}> {text}",
            "channel": channel,
            "user": _slack_user_id(speaker),
            "user_profile": {"display_name": speaker},
        }

    try:
        # --- start the meeting ------------------------------------------
        title = f"Rehearsal: {fixture_name.replace('_', ' ').title()}"
        captured = say.capture()
        await connector._handle_mention(mention_event(f'start meeting "{title}" as {role}'), say)
        result.intro = "\n".join(captured)
        say.stop()

        session = await connector._get_session(channel)
        if not session:
            result.crashed = "Meeting failed to start — check the role name."
            return result
        result.meeting_id = session.meeting.meeting_id

        # --- replay the transcript --------------------------------------
        start = clock.time()
        for event in events:
            clock.advance(pace_seconds)
            stamp = _mmss(clock.time() - start)
            speaker = event.speaker or "Unknown"
            addressed = _addresses_bot(event.content, participant_name)
            result.timeline.append(f"[{stamp}] {speaker}: {event.content}")

            captured = say.capture()
            if addressed:
                # Slack delivers a message mentioning the bot twice: once as
                # `app_mention` and once as `message`. Replaying both is how
                # the rehearsal proves the de-duplication guard holds.
                slack_event = mention_event(event.content, speaker)
                await connector._handle_mention(slack_event, say)
                await connector._handle_message(slack_event, say, {"bot_user_id": BOT_USER_ID})
            else:
                await connector._handle_message(
                    {
                        "text": event.content,
                        "channel": channel,
                        "user": _slack_user_id(speaker),
                        "user_profile": {"display_name": speaker},
                    },
                    say,
                    {"bot_user_id": BOT_USER_ID},
                )
            say.stop()

            for message in captured:
                result.timeline.append(f"[{stamp}] >>> {participant_name}: {message}")

        # --- duplicate check --------------------------------------------
        # Every replayed message must produce exactly one HUMAN transcript
        # row; the AI's own contributions are legitimately on the record too
        # (metadata ai_contribution) and are not duplicates. Anything beyond
        # that means a message was recorded twice — which is what happens if
        # Slack's dual delivery of an @-mention is not de-duplicated.
        transcript = db.get_transcript(result.meeting_id, limit=1000)
        human_rows = [e for e in transcript if not e.metadata.get("ai_contribution")]
        result.duplicate_events = max(0, len(human_rows) - result.replayed)

        # --- end the meeting --------------------------------------------
        clock.advance(pace_seconds)
        captured = say.capture()
        await connector._handle_mention(mention_event("end meeting"), say)
        result.post_meeting = list(captured)
        say.stop()

    except Exception as exc:  # the whole point is to find these here, not live
        result.crashed = f"{type(exc).__name__}: {exc}"

    return result


def format_result(result: RehearsalResult, verbose: bool) -> str:
    """Render one role's rehearsal as readable text."""
    lines = [
        "=" * 72,
        f"ROLE: {result.role}    FIXTURE: {result.fixture}",
        "=" * 72,
        "",
    ]

    if result.crashed:
        lines += [f"*** CRASHED: {result.crashed}", ""]
        return "\n".join(lines)

    if result.intro:
        lines += ["Introduction:", result.intro, ""]

    if verbose:
        lines += ["Timeline:", *result.timeline, ""]
    else:
        spoken = [line for line in result.timeline if ">>>" in line]
        lines += [
            f"Timeline: {result.replayed} messages replayed "
            f"({len(spoken)} AI contribution(s)). Use --verbose for the full transcript.",
            *spoken,
            "",
        ]

    if result.duplicate_events:
        lines += [
            f"*** {result.duplicate_events} duplicate transcript row(s) — "
            f"the same message was recorded more than once.",
            "",
        ]

    lines += ["Post-meeting output:"]
    if result.post_meeting:
        for message in result.post_meeting:
            body = message if verbose else message[:400] + ("…" if len(message) > 400 else "")
            lines.append(body)
            lines.append("")
    else:
        lines += ["*** none produced.", ""]

    return "\n".join(lines)


async def run(args: argparse.Namespace) -> str:
    """Run every requested rehearsal and return the assembled report."""
    modes = load_all_modes("config/modes")
    if not modes:
        return "No modes found in config/modes/.\n"

    roles = sorted(modes) if args.all else [args.role]
    unknown = [r for r in roles if r not in modes]
    if unknown:
        return f"Unknown role(s): {', '.join(unknown)}. Available: {', '.join(sorted(modes))}\n"

    db_path = Path(args.db)
    if args.fresh and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)

    if args.live:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key and os.environ.get("LLM_PROVIDER", "anthropic") == "anthropic":
            return "--live needs ANTHROPIC_API_KEY in the environment.\n"
        llm: LLMAdapter = create_adapter(
            {
                "provider": os.environ.get("LLM_PROVIDER", "anthropic"),
                "api_key": api_key,
                "model": os.environ.get("LLM_MODEL", ""),
                "base_url": os.environ.get("LLM_BASE_URL", ""),
                "workspace_id": os.environ.get("ANTHROPIC_WORKSPACE_ID", ""),
            }
        )
    else:
        llm = ScriptedLLMAdapter(
            detect_rate=args.detect_rate,
            detect_confidence=args.detect_confidence,
        )

    header = [
        "Synedrios Rehearsal",
        "=" * 72,
        f"Mode      : {'LIVE (real LLM calls)' if args.live else 'OFFLINE (scripted LLM, free)'}",
        f"LLM       : {llm.name}",
        f"Database  : {db_path}",
        f"Pace      : {args.pace:.0f} simulated seconds between messages",
        *(
            [
                "Throttle  : DISABLED — calibration mode. Speak rates here are "
                "meaningless; only detection recall is being measured."
            ]
            if args.no_throttle
            else []
        ),
        f"Roles     : {', '.join(roles)}",
        "",
    ]
    if not args.live:
        header += [
            "Offline detections are synthetic — they exercise the trigger paths,",
            "they do not judge detection quality. Use --live for that.",
            "",
        ]

    sections: list[str] = []
    for role in roles:
        fixture = args.fixture or DEFAULT_FIXTURES.get(role, "simple_standup")
        result = await rehearse_role(
            role=role,
            fixture_name=fixture,
            db=db,
            llm=llm,
            modes=modes,
            participant_name=args.name,
            pace_seconds=args.pace,
            no_throttle=args.no_throttle,
        )
        sections.append(format_result(result, args.verbose))

    calibration = format_report(build_rows(db))
    return "\n".join(header + sections + ["", calibration])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a meeting transcript through the full Synedrios stack."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--role", default="", help="Rehearse a single role.")
    group.add_argument("--all", action="store_true", help="Rehearse every configured role.")
    parser.add_argument(
        "--fixture",
        default="",
        help="Transcript fixture to replay (default: the one suited to each role).",
    )
    parser.add_argument("--live", action="store_true", help="Use the real LLM (costs money).")
    parser.add_argument(
        "--no-throttle",
        action="store_true",
        help=(
            "CALIBRATION ONLY: disable cooldown and rate limit so every "
            "detection surfaces and recall can be measured. Never represents "
            "production behaviour."
        ),
    )
    parser.add_argument(
        "--db",
        default=str(Path(tempfile.gettempdir()) / "synedrios-rehearsal.db"),
        help=(
            "Rehearsal database path. Defaults to a scratch file in the system "
            "temp directory: a rehearsal is not an audit record, and keeping it "
            "off the deployment volume keeps replays fast."
        ),
    )
    parser.add_argument("--fresh", action="store_true", help="Delete the rehearsal DB first.")
    parser.add_argument("--name", default="Synedrios", help="Participant display name.")
    parser.add_argument(
        "--pace",
        type=float,
        default=20.0,
        help="Simulated seconds between messages (drives cooldowns). Default 20.",
    )
    parser.add_argument(
        "--detect-rate",
        type=float,
        default=0.15,
        help="Offline only: share of messages the synthetic detectors flag.",
    )
    parser.add_argument(
        "--detect-confidence",
        type=float,
        default=0.95,
        help="Offline only: confidence the synthetic detectors report.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print the full transcript.")
    parser.add_argument("--out", default="", help="Also write the report to this file.")
    args = parser.parse_args(argv)

    load_env_file()  # for --live; offline mode needs no credentials
    os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-rehearsal")

    report = asyncio.run(run(args))
    print(report)
    if args.out:
        Path(args.out).write_text(report)
        print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
