"""Unit tests for the Slack connector (S01-S17).

Slack itself is mocked: the connector's command and event-handler
coroutines are driven directly with fake event dicts and a fake async
`say`. The database is real (temporary) so transcript and audit writes
are verified end-to-end. No network calls.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.connectors.slack import SlackConnector
from src.engine.modes import load_all_modes
from src.models import EventType, MeetingEvent, MeetingStatus, MemoryEntry
from src.persistence.database import Database
from tests.conftest import MockLLMAdapter

CHANNEL = "C123"
USER = "U999"


class FakeSay:
    """Records everything the connector tries to post to Slack."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)

    @property
    def joined(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def connector(temp_db: Database, monkeypatch) -> SlackConnector:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    return SlackConnector(
        db=temp_db,
        llm=MockLLMAdapter("Mock response."),
        modes=load_all_modes("config/modes"),
        participant_name="Synedrios",
    )


def _audit_types(db: Database, meeting_id: str) -> list[str]:
    conn = sqlite3.connect(str(db.db_path))
    try:
        rows = conn.execute(
            "SELECT event_type FROM audit_log WHERE meeting_id = ? ORDER BY timestamp, rowid",
            (meeting_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _audit_payloads(db: Database, meeting_id: str, event_type: str) -> list[dict]:
    import json

    conn = sqlite3.connect(str(db.db_path))
    try:
        rows = conn.execute(
            "SELECT payload FROM audit_log WHERE meeting_id = ? AND event_type = ? "
            "ORDER BY timestamp, rowid",
            (meeting_id, event_type),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


def _mention(text: str, channel: str = CHANNEL) -> dict:
    return {"text": f"<@U000BOT> {text}", "channel": channel, "user": USER}


def _message(text: str, channel: str = CHANNEL, **extra) -> dict:
    return {"text": text, "channel": channel, "user": USER, **extra}


# ----- Command parsing (S01-S08) -----


@pytest.mark.asyncio
async def test_s01_start_parses_title(connector: SlackConnector) -> None:
    """S01 — start meeting parses the title from quotes."""
    say = FakeSay()
    await connector._cmd_start('start meeting "Q3 Review" as secretary', CHANNEL, USER, say)
    meeting = connector.db.get_active_meeting_by_channel(CHANNEL)
    assert meeting is not None
    assert meeting.title == "Q3 Review"


@pytest.mark.asyncio
async def test_s02_start_parses_role(connector: SlackConnector) -> None:
    """S02 — start meeting parses the role name."""
    say = FakeSay()
    await connector._cmd_start('start meeting "X" as technical_advisor', CHANNEL, USER, say)
    assert connector._sessions[CHANNEL].engine.mode.name == "technical_advisor"


@pytest.mark.asyncio
async def test_s03_start_unknown_role_errors(connector: SlackConnector) -> None:
    """S03 — start meeting with an unknown role returns an error and starts nothing."""
    say = FakeSay()
    await connector._cmd_start('start meeting "X" as wizard', CHANNEL, USER, say)
    assert "Unknown role" in say.joined
    assert CHANNEL not in connector._sessions


@pytest.mark.asyncio
async def test_s04_start_duplicate_errors(connector: SlackConnector) -> None:
    """S04 — start meeting in a channel with an active meeting is rejected."""
    say = FakeSay()
    await connector._cmd_start('start meeting "First" as secretary', CHANNEL, USER, say)
    await connector._cmd_start('start meeting "Second" as secretary', CHANNEL, USER, say)
    assert "already active" in say.joined
    assert connector._sessions[CHANNEL].meeting.title == "First"


@pytest.mark.asyncio
async def test_s05_end_no_meeting_errors(connector: SlackConnector) -> None:
    """S05 — end meeting with no active meeting returns an error."""
    say = FakeSay()
    await connector._cmd_end(CHANNEL, USER, say)
    assert "No active meeting" in say.joined


@pytest.mark.asyncio
async def test_s06_end_triggers_post_meeting(connector: SlackConnector) -> None:
    """S06 — end meeting generates post-meeting outputs and marks the meeting ended."""
    say = FakeSay()
    await connector._cmd_start('start meeting "Standup" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector.db.save_event(
        meeting_id,
        MeetingEvent(event_type=EventType.CHAT, speaker="Nikos", content="We shipped the feature."),
    )
    end_say = FakeSay()
    await connector._cmd_end(CHANNEL, USER, end_say)
    assert "Structured Notes" in end_say.joined  # at least one output posted
    assert connector.db.get_meeting(meeting_id).status == MeetingStatus.ENDED
    assert CHANNEL not in connector._sessions


@pytest.mark.asyncio
async def test_s07_status_reports_meeting(connector: SlackConnector) -> None:
    """S07 — status returns current meeting info."""
    say = FakeSay()
    await connector._cmd_start('start meeting "Q3 Review" as secretary', CHANNEL, USER, say)
    status_say = FakeSay()
    await connector._cmd_status(CHANNEL, status_say)
    assert "Q3 Review" in status_say.joined


@pytest.mark.asyncio
async def test_s08_help_lists_roles(connector: SlackConnector) -> None:
    """S08 — help lists the available roles."""
    say = FakeSay()
    await connector._cmd_help(say)
    assert "secretary" in say.joined
    assert "facilitator" in say.joined


# ----- Event capture (S09-S13) -----


@pytest.mark.asyncio
async def test_s09_message_during_meeting_captured(connector: SlackConnector) -> None:
    """S09 — a regular message during an active meeting becomes a transcript event."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_message(_message("The build is green."), FakeSay())
    transcript = connector.db.get_transcript(meeting_id)
    assert [e.content for e in transcript] == ["The build is green."]


@pytest.mark.asyncio
async def test_s10_message_without_meeting_ignored(connector: SlackConnector) -> None:
    """S10 — a message when no meeting is active is ignored."""
    say = FakeSay()
    await connector._handle_message(_message("hello"), say)
    assert say.messages == []
    assert CHANNEL not in connector._sessions


@pytest.mark.asyncio
async def test_s11_bot_messages_ignored(connector: SlackConnector) -> None:
    """S11 — bot messages are ignored (no self-response loop)."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_message(_message("I am a bot", bot_id="B1"), FakeSay())
    assert connector.db.get_transcript(meeting_id) == []


@pytest.mark.asyncio
async def test_s12_edits_and_subtypes_ignored(connector: SlackConnector) -> None:
    """S12 — message edits and other subtypes are ignored."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_message(_message("edited text", subtype="message_changed"), FakeSay())
    assert connector.db.get_transcript(meeting_id) == []


@pytest.mark.asyncio
async def test_s13_mention_is_direct_address(connector: SlackConnector) -> None:
    """S13 — an @mention during a meeting is treated as direct address and answered."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    reply = FakeSay()
    await connector._handle_mention(_mention("what is the status?"), reply)
    assert "Mock response." in reply.joined


# ----- Audit completeness (S14-S16) -----


@pytest.mark.asyncio
async def test_s14_audit_for_transcript_segment(connector: SlackConnector) -> None:
    """S14 — every captured transcript segment writes an audit entry."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_message(_message("a normal message"), FakeSay())
    assert "transcript_segment" in _audit_types(connector.db, meeting_id)


@pytest.mark.asyncio
async def test_s15_audit_for_contribution(connector: SlackConnector) -> None:
    """S15 — every contribution writes an audit entry."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_mention(_mention("please respond"), FakeSay())
    assert "contribution_made" in _audit_types(connector.db, meeting_id)


@pytest.mark.asyncio
async def test_s16_audit_for_suppressed_trigger(connector: SlackConnector) -> None:
    """S16 — a trigger suppressed by turn management writes an audit entry."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    session = connector._sessions[CHANNEL]
    meeting_id = session.meeting.meeting_id
    # Exhaust turn management so the next trigger is suppressed by cooldown
    session.engine.record_contribution()
    await connector._handle_mention(_mention("respond again immediately"), FakeSay())
    assert "trigger_suppressed" in _audit_types(connector.db, meeting_id)


# ----- Platform limits (S17) -----


@pytest.mark.asyncio
async def test_s17_long_response_chunked(connector: SlackConnector) -> None:
    """S17 — long post-meeting outputs are chunked at 3800 chars for Slack."""
    connector.llm.next_response = "x" * 4200  # exceeds the 3800-char chunk size
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector.db.save_event(
        meeting_id, MeetingEvent(event_type=EventType.CHAT, speaker="Nikos", content="content")
    )
    end_say = FakeSay()
    await connector._cmd_end(CHANNEL, USER, end_say)
    # Chunk headers look like "(1/2)"; every posted chunk stays within the limit
    assert any("(1/" in m for m in end_say.messages)
    assert all(len(m) <= 3900 for m in end_say.messages)


# ----- LLM-based detection wiring (S18-S19, T2) -----

_FE_ASSERTION = "Redis is fundamentally a relational database engine."


@pytest.mark.asyncio
async def test_s18_below_threshold_detection_audited(connector: SlackConnector) -> None:
    """S18 — A below-threshold LLM detection is logged as a suppressed trigger."""
    # The detector reports an error but with confidence under the 0.9 threshold.
    connector.llm.next_response = (
        '{"error": true, "is_opinion": false, "confidence": 0.55, "correction": "x"}'
    )
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as technical_advisor', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    await connector._handle_message(_message(_FE_ASSERTION), FakeSay())

    suppressed = _audit_payloads(connector.db, meeting_id, "trigger_suppressed")
    assert any(
        p.get("reason") == "below_threshold" and p.get("trigger") == "factual_error"
        for p in suppressed
    )
    # Below threshold means no contribution was made
    assert "contribution_made" not in _audit_types(connector.db, meeting_id)


@pytest.mark.asyncio
async def test_s19_fired_detection_contributes(connector: SlackConnector) -> None:
    """S19 — An above-threshold LLM detection produces an audited contribution."""
    connector.llm.next_response = (
        '{"error": true, "is_opinion": false, "confidence": 0.97, '
        '"correction": "Redis is a key-value store."}'
    )
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as technical_advisor', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    reply = FakeSay()
    await connector._handle_message(_message(_FE_ASSERTION), reply)
    assert reply.messages  # the AI posted a contribution
    assert "contribution_made" in _audit_types(connector.db, meeting_id)


# ----- Memory read/write across meetings (S20-S22, T3) -----

_MEM_DECISION = (
    '{"memories": [{"type": "decision", '
    '"content": "Chose PostgreSQL for the orders service.", '
    '"confidence": 0.95, "status": "new"}]}'
)


@pytest.mark.asyncio
async def test_s20_end_meeting_writes_memories(connector: SlackConnector) -> None:
    """S20 — Ending a meeting extracts, stores, and audits memories."""
    connector.llm.next_response = _MEM_DECISION
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector.db.save_event(
        meeting_id,
        MeetingEvent(event_type=EventType.CHAT, speaker="Nikos", content="We chose PostgreSQL."),
    )
    await connector._cmd_end(CHANNEL, USER, FakeSay())

    pid = connector._participant.participant_id
    memories = connector.db.get_memories(pid)
    assert any("PostgreSQL" in m.content for m in memories)
    assert "memory_created" in _audit_types(connector.db, meeting_id)


@pytest.mark.asyncio
async def test_s21_start_meeting_injects_memory(connector: SlackConnector) -> None:
    """S21 — Starting a meeting injects the participant's stored memories."""
    pid = connector._participant.participant_id
    connector.db.save_memory(
        MemoryEntry(
            participant_id=pid,
            memory_type="preference",
            content="Sarah prefers PostgreSQL.",
            confidence=0.9,
        )
    )
    say = FakeSay()
    await connector._cmd_start('start meeting "M2" as technical_advisor', CHANNEL, USER, say)
    engine = connector._sessions[CHANNEL].engine
    assert any("Sarah prefers PostgreSQL" in m for m in engine.injected_memories)


@pytest.mark.asyncio
async def test_s22_supersession_demotes_old_memory(connector: SlackConnector) -> None:
    """S22 — An 'update' memory demotes the superseded memory's confidence."""
    pid = connector._participant.participant_id
    connector.db.save_memory(
        MemoryEntry(
            participant_id=pid,
            memory_type="fact",
            content="The API limit is 100 rps.",
            confidence=0.9,
        )
    )
    connector.llm.next_response = (
        '{"memories": [{"type": "fact", "content": "The API limit is 20 rps.", '
        '"confidence": 0.95, "status": "update", '
        '"supersedes": "The API limit is 100 rps."}]}'
    )
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector.db.save_event(
        meeting_id,
        MeetingEvent(
            event_type=EventType.CHAT, speaker="Dana", content="Actually the limit is 20 rps."
        ),
    )
    await connector._cmd_end(CHANNEL, USER, FakeSay())

    by_content = {m.content: m.confidence for m in connector.db.get_memories(pid)}
    assert by_content["The API limit is 100 rps."] == 0.3  # demoted
    assert by_content["The API limit is 20 rps."] == 0.95  # newly stored


# --------------------------------------------------------------------------
# Live-run hardening (S23-S27) — added in T6
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s23_mention_is_not_recorded_twice(connector: SlackConnector) -> None:
    """S23 — Slack delivers a mention twice; only one transcript row results.

    A message that @-mentions the bot arrives as both `app_mention` and
    `message`. Handling both would record the message twice and could draw two
    replies to one question.
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id

    bot = "U000BOT"
    connector._bot_user_id = bot
    event = {
        "text": f"<@{bot}> what did we decide?",
        "channel": CHANNEL,
        "user": USER,
        "user_profile": {"display_name": "Nikos"},
    }

    await connector._handle_mention(event, say)
    await connector._handle_message(event, say, {"bot_user_id": bot})

    transcript = connector.db.get_transcript(meeting_id)
    human_rows = [e for e in transcript if not e.metadata.get("ai_contribution")]
    assert len(human_rows) == 1
    assert human_rows[0].content == "what did we decide?"
    # The AI replied to the direct address — its reply is also on the record
    # (one AI row, not two: the dedup guard held).
    ai_rows = [e for e in transcript if e.metadata.get("ai_contribution")]
    assert len(ai_rows) == 1


@pytest.mark.asyncio
async def test_s24_plain_message_still_recorded(connector: SlackConnector) -> None:
    """S24 — The mention guard must not swallow ordinary messages."""
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector._bot_user_id = "U000BOT"

    await connector._handle_message(
        {"text": "The build is green.", "channel": CHANNEL, "user": USER},
        say,
        {"bot_user_id": "U000BOT"},
    )
    assert [e.content for e in connector.db.get_transcript(meeting_id)] == ["The build is green."]


@pytest.mark.asyncio
async def test_s25_session_rehydrates_after_restart(connector: SlackConnector) -> None:
    """S25 — A meeting survives losing the in-process session.

    Stateless workers, stateful database: any worker must be able to pick up an
    active meeting from the database rather than telling the room there is no
    meeting.
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "Q3 Planning" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id

    connector._sessions.clear()  # simulate a restart

    session = await connector._get_session(CHANNEL)
    assert session is not None
    assert session.meeting.meeting_id == meeting_id
    assert session.engine.mode.name == "secretary"

    # And the meeting can still be ended, rather than being orphaned as active.
    await connector._cmd_end(CHANNEL, USER, FakeSay())
    assert connector.db.get_meeting(meeting_id).status is MeetingStatus.ENDED


@pytest.mark.asyncio
async def test_s26_rehydration_restores_turn_state(connector: SlackConnector) -> None:
    """S26 — Cooldown and refractory state are rebuilt from the audit log.

    Otherwise a restart would hand the AI a clean slate and let it speak
    immediately, regardless of how recently it last spoke.
    """
    from src.models import AuditEntry, AuditEventType

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as facilitator', CHANNEL, USER, say)
    session = connector._sessions[CHANNEL]
    meeting_id = session.meeting.meeting_id
    pid = connector._participant.participant_id

    for i in range(3):
        connector.db.append_audit(
            AuditEntry(
                participant_id=pid,
                meeting_id=meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"speaker": "Nikos", "content": f"m{i}"},
            )
        )
    connector.db.append_audit(
        AuditEntry(
            participant_id=pid,
            meeting_id=meeting_id,
            event_type=AuditEventType.CONTRIBUTION_MADE,
            payload={"trigger": "synthesis_opportunity"},
        )
    )

    connector._sessions.clear()
    engine = (await connector._get_session(CHANNEL)).engine

    assert engine._contribution_count == 1
    assert engine._messages_seen == 3
    assert engine._last_contribution_at["synthesis_opportunity"] == 3
    assert not engine.can_contribute()  # the cooldown it was under still applies


@pytest.mark.asyncio
async def test_s27_meeting_title_keeps_its_casing(connector: SlackConnector) -> None:
    """S27 — A quoted title is stored as typed, not lower-cased.

    The title leads every post-meeting document, so mangling it is visible in
    every output the customer reads.
    """
    say = FakeSay()
    await connector._handle_mention(
        {
            "text": '<@U000BOT> start meeting "Q3 Roadmap Review" AS Secretary',
            "channel": CHANNEL,
            "user": USER,
        },
        say,
    )
    session = connector._sessions[CHANNEL]
    assert session.meeting.title == "Q3 Roadmap Review"
    assert session.engine.mode.name == "secretary"


@pytest.mark.asyncio
async def test_s28_speaker_names_resolve_from_slack(connector: SlackConnector) -> None:
    """S28 — A user id is resolved to a display name, and cached.

    Slack's message events do not reliably carry a user profile. Without a
    lookup the transcript records raw ids, and every action item in the notes
    is then owned by "U08KX3L4Q2".
    """
    calls: list[str] = []

    async def fake_users_info(user: str) -> dict:
        calls.append(user)
        return {"user": {"profile": {"display_name": "Sarah"}}}

    connector.app.client.users_info = fake_users_info  # type: ignore[assignment]
    connector._bot_user_id = "U000BOT"

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id

    for text in ("Migration is done.", "Rollback next."):
        await connector._handle_message(
            {"text": text, "channel": CHANNEL, "user": "U08KX3L4Q2"},
            say,
            {"bot_user_id": "U000BOT"},
        )

    assert [e.speaker for e in connector.db.get_transcript(meeting_id)] == ["Sarah", "Sarah"]
    assert calls == ["U08KX3L4Q2"]  # resolved once, then cached


@pytest.mark.asyncio
async def test_s29_speaker_falls_back_to_id(connector: SlackConnector) -> None:
    """S29 — A failed lookup degrades to the raw id rather than crashing."""

    async def failing_users_info(user: str) -> dict:
        raise RuntimeError("missing users:read scope")

    connector.app.client.users_info = failing_users_info  # type: ignore[assignment]
    connector._bot_user_id = "U000BOT"

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id

    await connector._handle_message(
        {"text": "Still recorded.", "channel": CHANNEL, "user": "U08KX3L4Q2"},
        say,
        {"bot_user_id": "U000BOT"},
    )
    assert [e.speaker for e in connector.db.get_transcript(meeting_id)] == ["U08KX3L4Q2"]


@pytest.mark.asyncio
async def test_s30_mentions_become_names_in_the_transcript(connector: SlackConnector) -> None:
    """S30 — `<@Uxxx>` is rendered as a name and recorded as the addressee.

    The transcript is what every post-meeting document is written from, so it
    should read "Sarah, can you check the migration?" rather than have the
    mention deleted — and the engine needs to know the question had a name on
    it.
    """

    async def fake_users_info(user: str) -> dict:
        return {"user": {"profile": {"display_name": {"U0SARAH": "Sarah"}[user]}}}

    connector.app.client.users_info = fake_users_info  # type: ignore[assignment]
    connector._bot_user_id = "U000BOT"
    connector._user_names["U999"] = "Nikos"

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as technical_advisor', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id

    await connector._handle_message(
        {
            "text": "<@U0SARAH> can you confirm the index is rebuilt?",
            "channel": CHANNEL,
            "user": USER,
        },
        say,
        {"bot_user_id": "U000BOT"},
    )

    recorded = connector.db.get_transcript(meeting_id)[0]
    assert recorded.content == "Sarah can you confirm the index is rebuilt?"
    assert recorded.metadata["directed_at"] == ["Sarah"]


@pytest.mark.asyncio
async def test_s31_colleagues_question_draws_no_reply(connector: SlackConnector) -> None:
    """S31 — A question aimed at a colleague produces no contribution.

    End to end through the connector: a reactive role stays out of a question
    one person put to another.
    """

    async def fake_users_info(user: str) -> dict:
        return {"user": {"profile": {"display_name": "Sarah"}}}

    connector.app.client.users_info = fake_users_info  # type: ignore[assignment]
    connector._bot_user_id = "U000BOT"
    connector._user_names["U999"] = "Nikos"

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as technical_advisor', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    posted_before = len(say.messages)

    await connector._handle_message(
        {"text": "<@U0SARAH> is the index rebuilt?", "channel": CHANNEL, "user": USER},
        say,
        {"bot_user_id": "U000BOT"},
    )

    assert len(say.messages) == posted_before  # said nothing
    assert _audit_types(connector.db, meeting_id).count("contribution_made") == 0


# --------------------------------------------------------------------------
# Live-rehearsal fixes (S32-S35) — added in T6, Session 9
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s32_ai_contribution_joins_the_transcript(connector: SlackConnector) -> None:
    """S32 — What the AI says becomes part of the meeting record.

    In the live rehearsal the advisor's own action-items document declared
    "No AI participant was present" after it had spoken five times — because
    the transcript handed to document generation never contained its words.
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector._bot_user_id = "U000BOT"

    await connector._handle_mention(
        {"text": "<@U000BOT> Synedrios, what did we decide?", "channel": CHANNEL, "user": USER},
        say,
    )

    ai_rows = [
        e for e in connector.db.get_transcript(meeting_id) if e.metadata.get("ai_contribution")
    ]
    assert len(ai_rows) == 1
    assert ai_rows[0].speaker == "Synedrios"
    assert ai_rows[0].content == "Mock response."


def test_s33_chunker_splits_at_natural_boundaries() -> None:
    """S33 — Long documents split on paragraphs, never mid-word.

    The naive slice cut "**conditional contingency**" into "**condition" and
    "al contingency**" across two Slack messages.
    """
    from src.connectors.slack import _chunk_text

    paragraphs = [f"Paragraph {i}: " + ("word " * 80).strip() + "." for i in range(12)]
    content = "\n\n".join(paragraphs)
    chunks = _chunk_text(content, limit=1000)

    assert len(chunks) > 1
    assert "".join(c.replace("\n", " ") for c in chunks).count("Paragraph") == 12
    for chunk in chunks:
        assert len(chunk) <= 1000
        # Every chunk ends at a word boundary — its last character is not
        # mid-word (next chunk never starts with a letter continuing it).
        assert not chunk.endswith(" ")
    for a, b in zip(chunks, chunks[1:]):
        assert a[-1] in ".…" or not b[:1].islower() or b[:1] == " " or a.endswith(".")

    # Short content passes through untouched.
    assert _chunk_text("short", limit=1000) == ["short"]


@pytest.mark.asyncio
async def test_s34_throttled_suppression_records_what_was_silenced(
    connector: SlackConnector,
) -> None:
    """S34 — A cooldown suppression carries trigger types and confidences.

    The first live rehearsal produced 9 bare "cooldown_or_rate_limit" entries
    that hid whether detection had worked at all on 4 of 6 planted errors.
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as technical_advisor', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector._bot_user_id = "U000BOT"
    engine = connector._sessions[CHANNEL].engine
    engine.record_contribution()  # start the cooldown clock

    await connector._handle_message(
        {"text": "Is sharding the right call here?", "channel": CHANNEL, "user": USER},
        say,
        {"bot_user_id": "U000BOT"},
    )

    payloads = _audit_payloads(connector.db, meeting_id, "trigger_suppressed")
    throttled = [p for p in payloads if p.get("reason") == "cooldown_or_rate_limit"]
    assert throttled, "expected a throttle suppression"
    entry = throttled[0]
    assert entry["all_triggers"][0]["type"] == "open_question"
    assert "confidence" in entry["all_triggers"][0]


@pytest.mark.asyncio
async def test_s35_memory_extraction_failure_is_audited(connector: SlackConnector) -> None:
    """S35 — An unparseable extraction response leaves an audit trace.

    The silent-observer rehearsal lost every memory from a 100-message
    meeting with zero visibility at any layer.
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector.db.save_event(
        meeting_id,
        MeetingEvent(event_type=EventType.CHAT, speaker="Nikos", content="We decided X."),
    )
    connector.llm.next_response = "sorry, no JSON today"

    await connector._cmd_end(CHANNEL, USER, FakeSay())

    types = _audit_types(connector.db, meeting_id)
    assert "memory_extraction_failed" in types
    payload = _audit_payloads(connector.db, meeting_id, "memory_extraction_failed")[0]
    assert "unparseable" in payload["error"]


@pytest.mark.asyncio
async def test_s36_empty_response_is_audited_as_withheld(connector: SlackConnector) -> None:
    """S36 — A fired trigger whose generation returns nothing leaves a record.

    Compliance-audit finding: the `if response:` path had no else — an empty
    reply (a real truncation/refusal failure mode) produced no audit entry at
    all, violating "every suppressed contribution is logged".
    """
    say = FakeSay()
    await connector._cmd_start('start meeting "M" as secretary', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    connector._bot_user_id = "U000BOT"
    connector.llm.next_response = "   "  # strips to empty

    posted_before = len(say.messages)
    await connector._handle_mention(
        {"text": "<@U000BOT> Synedrios, thoughts?", "channel": CHANNEL, "user": USER}, say
    )

    assert len(say.messages) == posted_before  # nothing posted
    types = _audit_types(connector.db, meeting_id)
    assert "contribution_withheld" in types
    payload = _audit_payloads(connector.db, meeting_id, "contribution_withheld")[0]
    assert payload["reason"] == "empty_response"


@pytest.mark.asyncio
async def test_s37_rehydrated_clock_skips_addressed_messages(
    connector: SlackConnector,
) -> None:
    """S37 — The rebuilt refractory clock matches a never-restarted worker.

    Addressed messages bypass trigger evaluation live and must not advance
    the message clock during rehydration either.
    """
    from src.models import AuditEntry, AuditEventType

    say = FakeSay()
    await connector._cmd_start('start meeting "M" as facilitator', CHANNEL, USER, say)
    meeting_id = connector._sessions[CHANNEL].meeting.meeting_id
    pid = connector._participant.participant_id

    for i, addressed in enumerate([False, True, False, True, False]):
        connector.db.append_audit(
            AuditEntry(
                participant_id=pid,
                meeting_id=meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"speaker": "Nikos", "content": f"m{i}", "addressed": addressed},
            )
        )

    connector._sessions.clear()
    engine = (await connector._get_session(CHANNEL)).engine
    assert engine._messages_seen == 3  # only the unaddressed ones
