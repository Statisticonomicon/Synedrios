"""Unit tests for core data models (M01-M07).

Covers model instantiation, the audit hash chain, tamper detection,
sensible defaults, trigger sorting, and enum integrity.
"""

from __future__ import annotations

from src.models import (
    AuditEntry,
    AuditEventType,
    AuthorityLevel,
    EventType,
    InitiativeLevel,
    MeetingEvent,
    MeetingStatus,
    Participant,
    TriggerResult,
)


def test_m01_create_meeting_event() -> None:
    """M01 — Create MeetingEvent with all fields; defaults and UUID generation."""
    event = MeetingEvent(
        event_type=EventType.CHAT,
        speaker="Nikos",
        speaker_id="U123",
        content="Hello team.",
        confidence=0.95,
        metadata={"source": "slack"},
    )
    assert event.speaker == "Nikos"
    assert event.content == "Hello team."
    assert event.confidence == 0.95
    assert event.metadata == {"source": "slack"}
    # UUID and timestamp are auto-generated and unique
    assert event.event_id
    assert event.timestamp is not None
    other = MeetingEvent(event_type=EventType.CHAT)
    assert event.event_id != other.event_id
    # Defaults
    assert other.speaker is None
    assert other.content == ""
    assert other.confidence == 1.0


def test_m02_audit_hash_deterministic() -> None:
    """M02 — Create AuditEntry, compute hash; hash is deterministic."""
    entry = AuditEntry(
        audit_id="fixed-id",
        meeting_id="m1",
        event_type=AuditEventType.TRIGGER_FIRED,
        payload={"trigger": "addressed_directly"},
    )
    h1 = entry.compute_hash()
    h2 = entry.compute_hash()
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_m03_finalise_chains_hashes() -> None:
    """M03 — finalise() links previous_hash to the prior entry's entry_hash."""
    first = AuditEntry(meeting_id="m1", event_type=AuditEventType.MEETING_STARTED)
    first.finalise("")
    assert first.previous_hash == ""
    assert first.entry_hash

    second = AuditEntry(meeting_id="m1", event_type=AuditEventType.TRANSCRIPT_SEGMENT)
    second.finalise(first.entry_hash)
    assert second.previous_hash == first.entry_hash
    assert second.entry_hash != first.entry_hash


def test_m04_tamper_changes_hash() -> None:
    """M04 — Tampering with the payload changes the hash; tampering is detectable."""
    entry = AuditEntry(
        meeting_id="m1",
        event_type=AuditEventType.CONTRIBUTION_MADE,
        payload={"response": "original"},
    )
    original = entry.finalise("").entry_hash
    # Tamper after finalisation
    entry.payload = {"response": "tampered"}
    recomputed = entry.compute_hash()
    assert recomputed != original


def test_m05_participant_defaults() -> None:
    """M05 — Participant defaults are sensible."""
    p = Participant()
    assert p.organisation_id == "default"
    assert p.name == "Synedrios"
    assert p.default_role == "secretary"
    assert p.default_tone["directness"] == 2
    assert p.participant_id


def test_m06_trigger_result_sort_by_priority() -> None:
    """M06 — TriggerResult sorts by priority (lower number first)."""
    triggers = [
        TriggerResult(trigger_type="synthesis", fired=True, priority=4),
        TriggerResult(trigger_type="addressed", fired=True, priority=1),
        TriggerResult(trigger_type="circular", fired=True, priority=3),
    ]
    triggers.sort(key=lambda t: t.priority)
    assert [t.trigger_type for t in triggers] == ["addressed", "circular", "synthesis"]


def test_m07_enum_values() -> None:
    """M07 — All enums have the expected values (no typos)."""
    assert InitiativeLevel.SILENT == 0
    assert InitiativeLevel.LEAD == 6
    assert AuthorityLevel.OBSERVER == 1
    assert AuthorityLevel.CHAIR == 5
    assert MeetingStatus.ACTIVE.value == "active"
    assert EventType.CHAT.value == "chat"
    assert AuditEventType.TRIGGER_SUPPRESSED.value == "trigger_suppressed"
    # Spot-check completeness of the initiative ladder (0..6)
    assert {lvl.value for lvl in InitiativeLevel} == set(range(7))
