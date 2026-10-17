"""Integration tests (I01-I09) — real LLM API calls.

These are marked `@pytest.mark.integration` and skipped unless pytest is
run with `--run-integration`. They cost money and need API keys, so they
are run manually before a release, not in CI.

    pytest tests/test_integration.py -v --run-integration

Each test skips cleanly if the required API key is absent.
"""

from __future__ import annotations

import os

import httpx
import pytest

from src.engine.modes import load_all_modes
from src.engine.participation import ParticipationEngine
from src.llm.adapter import AnthropicAdapter, create_adapter
from src.models import (
    AuditEntry,
    AuditEventType,
    EventType,
    MeetingEvent,
    TriggerResult,
)
from src.persistence.database import Database
from tests.conftest import load_fixture

pytestmark = pytest.mark.integration

MODES = load_all_modes("config/modes")


def _anthropic() -> AnthropicAdapter:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return create_adapter({"provider": "anthropic", "api_key": key})


def _engine(role: str, llm) -> ParticipationEngine:
    return ParticipationEngine(
        mode=MODES[role],
        llm=llm,
        participant_name="Synedrios",
        participant_id="p1",
        meeting_id="m1",
    )


@pytest.mark.asyncio
async def test_i01_full_meeting_simulation(tmp_path) -> None:
    """I01 — start → 10 messages → end produces post-meeting outputs."""
    llm = _anthropic()
    db = Database(tmp_path / "i01.db")
    db.get_default_participant()  # ensure the default avatar exists
    engine = _engine("secretary", llm)
    transcript = load_fixture("simple_standup")[:10]
    outputs = await engine.generate_post_meeting(transcript, "Standup")
    assert outputs
    assert all(text.strip() for text in outputs.values())


@pytest.mark.asyncio
async def test_i02_secretary_coherent_notes() -> None:
    """I02 — Secretary produces coherent notes referencing the transcript."""
    engine = _engine("secretary", _anthropic())
    outputs = await engine.generate_post_meeting(
        load_fixture("simple_standup"), "Engineering Standup"
    )
    notes = outputs.get("structured_notes", "")
    assert len(notes) > 100
    # Should reference at least one participant by name
    assert any(name in notes for name in ("Sarah", "Mike", "Priya", "Tom", "Nikos"))


@pytest.mark.asyncio
async def test_i03_technical_advisor_responds_technically() -> None:
    """I03 — Technical advisor answers a direct technical address with substance."""
    engine = _engine("technical_advisor", _anthropic())
    transcript = load_fixture("technical_debate")
    event = MeetingEvent(
        event_type=EventType.CHAT,
        speaker="Nikos",
        content="Synedrios, is PostgreSQL a sensible default for this orders service?",
    )
    trigger = TriggerResult(
        trigger_type="addressed_directly", fired=True, priority=1, reason="Direct address"
    )
    response = await engine.generate_response(trigger, event, transcript)
    assert len(response) > 20


@pytest.mark.asyncio
async def test_i04_silent_observer_outputs_only() -> None:
    """I04 — Silent observer never triggers live but still produces outputs."""
    engine = _engine("silent_observer", _anthropic())
    transcript = load_fixture("long_planning")
    # No live contribution: zero triggers regardless of input
    assert engine.evaluate_triggers(transcript[-1], transcript) == []
    outputs = await engine.generate_post_meeting(transcript, "Q3 Planning")
    assert "structured_notes" in outputs and outputs["structured_notes"].strip()


@pytest.mark.asyncio
async def test_i05_action_items_have_owners() -> None:
    """I05 — Action-item extraction identifies owners and tasks."""
    engine = _engine("secretary", _anthropic())
    outputs = await engine.generate_post_meeting(load_fixture("simple_standup"), "Standup")
    actions = outputs.get("action_items_by_owner", "")
    assert any(name in actions for name in ("Sarah", "Mike", "Tom", "Nikos"))


@pytest.mark.asyncio
async def test_i06_audit_chain_valid_after_meeting(tmp_path) -> None:
    """I06 — The audit chain is valid after a full meeting's worth of entries."""
    db = Database(tmp_path / "i06.db")
    participant = db.get_default_participant()
    from src.models import Meeting, MeetingStatus

    meeting = Meeting(participant_id=participant.participant_id, status=MeetingStatus.ACTIVE)
    db.create_meeting(meeting)
    for i in range(12):
        db.append_audit(
            AuditEntry(
                participant_id=participant.participant_id,
                meeting_id=meeting.meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"i": i},
            )
        )
    # Verify the chain
    import sqlite3

    conn = sqlite3.connect(str(db.db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE meeting_id=? ORDER BY timestamp, rowid",
        (meeting.meeting_id,),
    ).fetchall()
    conn.close()
    prev = ""
    for row in rows:
        assert row["previous_hash"] == prev
        prev = row["entry_hash"]
    assert len(rows) == 12


@pytest.mark.asyncio
async def test_i07_large_meeting_within_context() -> None:
    """I07 — A 100-message meeting generates a response without context errors."""
    engine = _engine("technical_advisor", _anthropic())
    transcript = load_fixture("long_planning")  # 100 events
    assert len(transcript) >= 50
    event = transcript[-1]
    trigger = TriggerResult(
        trigger_type="addressed_directly", fired=True, priority=1, reason="wrap-up"
    )
    response = await engine.generate_response(trigger, event, transcript)
    assert response.strip()


@pytest.mark.asyncio
async def test_i08_adapter_handles_api_error() -> None:
    """I08 — The adapter surfaces API errors (e.g. bad model) as HTTPStatusError."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    adapter = create_adapter(
        {"provider": "anthropic", "api_key": key, "model": "claude-nonexistent-model-x"}
    )
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.complete(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_i09_openai_adapter_same_fixture() -> None:
    """I09 — The OpenAI-compatible adapter produces outputs from the same fixture."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    llm = create_adapter({"provider": "openai", "api_key": key, "model": "gpt-4o-mini"})
    engine = _engine("secretary", llm)
    outputs = await engine.generate_post_meeting(load_fixture("quick_decision"), "DB Decision")
    assert outputs.get("structured_notes", "").strip()


# ----- LLM-based detection quality (I10-I12, T2) -----


@pytest.mark.asyncio
async def test_i10_factual_error_fires_on_planted_error() -> None:
    """I10 — factual_error fires on a planted error in technical_debate."""
    engine = _engine("technical_advisor", _anthropic())
    transcript = load_fixture("technical_debate")
    idx = next(
        i
        for i, e in enumerate(transcript)
        if "Redis is fundamentally a relational database" in e.content
    )
    results = await engine.evaluate_llm_triggers(transcript[idx], transcript[: idx + 1])
    assert any(r.trigger_type == "factual_error" and r.fired for r in results), results


@pytest.mark.asyncio
async def test_i11_no_false_positives_on_standup() -> None:
    """I11 — factual_error fires zero times on the error-free standup."""
    engine = _engine("technical_advisor", _anthropic())
    transcript = load_fixture("simple_standup")
    fired = 0
    for i, event in enumerate(transcript):
        results = await engine.evaluate_llm_triggers(event, transcript[: i + 1])
        fired += sum(1 for r in results if r.trigger_type == "factual_error" and r.fired)
    assert fired == 0


@pytest.mark.asyncio
async def test_i12_contradiction_detected() -> None:
    """I12 — contradiction fires on the purpose-built contradiction fixture."""
    engine = _engine("technical_advisor", _anthropic())
    transcript = load_fixture("contradiction")
    results = await engine.evaluate_llm_triggers(transcript[-1], transcript)
    assert any(r.trigger_type == "contradiction" and r.fired for r in results), results


# ----- Cross-meeting memory recall (I13, T3) -----


@pytest.mark.asyncio
async def test_i13_cross_meeting_recall() -> None:
    """I13 — Memory extracted in one meeting is recalled in the next."""
    llm = _anthropic()
    # Meeting 1 decides on PostgreSQL; extract durable memory from it.
    extractor = _engine("secretary", llm)
    memories = await extractor.extract_memories(load_fixture("quick_decision"), [])
    assert memories, "expected at least one durable memory from the decision meeting"

    # Meeting 2: inject that memory and ask the avatar to recall the decision.
    engine = _engine("technical_advisor", llm)
    engine.injected_memories = [f"[{m.memory_type}] {m.content}" for m in memories]
    trigger = TriggerResult(
        trigger_type="addressed_directly", fired=True, priority=1, reason="Direct address"
    )
    event = MeetingEvent(
        event_type=EventType.CHAT,
        speaker="Nikos",
        content="Synedrios, remind me which database we settled on last time.",
    )
    response = await engine.generate_response(trigger, event, [event])
    assert "postgres" in response.lower(), response
