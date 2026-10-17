"""Unit tests for the SQLite persistence layer (D01-D15).

Covers schema creation, round-trip integrity for every entity, the
append-only hash-chained audit log, per-meeting chain isolation,
organisation_id persistence, and concurrent writes under WAL.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

from src.models import (
    ActionItem,
    AuditEntry,
    AuditEventType,
    EventType,
    Meeting,
    MeetingEvent,
    MeetingStatus,
    MemoryEntry,
    Participant,
    utc_now,
)
from src.persistence.database import Database


def _audit_rows(db: Database, meeting_id: str) -> list[sqlite3.Row]:
    """Read raw audit rows for a meeting, oldest first (test helper)."""
    conn = sqlite3.connect(str(db.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM audit_log WHERE meeting_id = ? ORDER BY timestamp ASC, rowid ASC",
            (meeting_id,),
        ).fetchall()
    finally:
        conn.close()


def _meeting(db: Database, **kwargs) -> Meeting:
    """Create a persisted meeting bound to the default participant.

    Meetings carry a FOREIGN KEY to participants, so a valid participant
    must exist first — this mirrors the production Slack flow, which always
    resolves the default participant before starting a meeting.
    """
    kwargs.setdefault("participant_id", db.get_default_participant().participant_id)
    kwargs.setdefault("status", MeetingStatus.ACTIVE)
    m = Meeting(**kwargs)
    db.create_meeting(m)
    return m


def test_d01_schema_creation(tmp_path: Path) -> None:
    """D01 — Schema is created on a fresh database with no errors."""
    db = Database(tmp_path / "fresh.db")
    conn = sqlite3.connect(str(db.db_path))
    try:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert {
        "participants",
        "meetings",
        "transcript",
        "action_items",
        "audit_log",
        "memories",
    } <= names


def test_d02_participant_round_trip(temp_db: Database) -> None:
    """D02 — Create and retrieve a participant without data loss."""
    p = Participant(name="Aria", default_role="coach")
    temp_db.create_participant(p)
    loaded = temp_db.get_participant(p.participant_id)
    assert loaded is not None
    assert loaded.name == "Aria"
    assert loaded.default_role == "coach"
    assert loaded.participant_id == p.participant_id


def test_d03_default_participant_auto_created(temp_db: Database) -> None:
    """D03 — get_default_participant creates one on first call."""
    p = temp_db.get_default_participant()
    assert p is not None
    assert p.organisation_id == "default"


def test_d04_default_participant_idempotent(temp_db: Database) -> None:
    """D04 — get_default_participant returns the same participant twice."""
    first = temp_db.get_default_participant()
    second = temp_db.get_default_participant()
    assert first.participant_id == second.participant_id


def test_d05_meeting_round_trip(temp_db: Database) -> None:
    """D05 — Create and retrieve a meeting; all fields preserved."""
    m = _meeting(temp_db, title="Q3 Review", channel_id="C1", started_at=utc_now())
    loaded = temp_db.get_meeting(m.meeting_id)
    assert loaded is not None
    assert loaded.title == "Q3 Review"
    assert loaded.channel_id == "C1"
    assert loaded.status == MeetingStatus.ACTIVE


def test_d06_update_meeting_status(temp_db: Database) -> None:
    """D06 — update_meeting_status changes status and ended_at."""
    m = _meeting(temp_db, title="x")
    ended = utc_now()
    temp_db.update_meeting_status(m.meeting_id, MeetingStatus.ENDED, ended)
    loaded = temp_db.get_meeting(m.meeting_id)
    assert loaded.status == MeetingStatus.ENDED
    assert loaded.ended_at is not None


def test_d07_active_meeting_by_channel(temp_db: Database) -> None:
    """D07 — get_active_meeting_by_channel finds active, ignores ended."""
    _meeting(temp_db, title="old", channel_id="C1", status=MeetingStatus.ENDED)
    active = _meeting(temp_db, title="new", channel_id="C1", status=MeetingStatus.ACTIVE)
    found = temp_db.get_active_meeting_by_channel("C1")
    assert found is not None
    assert found.meeting_id == active.meeting_id
    assert temp_db.get_active_meeting_by_channel("C-none") is None


def test_d08_save_and_get_transcript_ordered(temp_db: Database) -> None:
    """D08 — save_event and get_transcript return events in timestamp order."""
    m = _meeting(temp_db, title="x")
    base = utc_now()
    # Insert out of order; expect chronological retrieval
    for i in (2, 0, 1):
        temp_db.save_event(
            m.meeting_id,
            MeetingEvent(
                event_type=EventType.CHAT,
                speaker=f"S{i}",
                content=f"msg {i}",
                timestamp=base + timedelta(seconds=i),
            ),
        )
    transcript = temp_db.get_transcript(m.meeting_id)
    assert [e.content for e in transcript] == ["msg 0", "msg 1", "msg 2"]
    assert transcript[0].event_type == EventType.CHAT


def test_d09_recent_transcript_last_n_in_order(temp_db: Database) -> None:
    """D09 — get_recent_transcript returns the last N events, chronologically."""
    m = _meeting(temp_db, title="x")
    base = utc_now()
    for i in range(10):
        temp_db.save_event(
            m.meeting_id,
            MeetingEvent(
                event_type=EventType.CHAT,
                speaker="S",
                content=f"m{i}",
                timestamp=base + timedelta(seconds=i),
            ),
        )
    recent = temp_db.get_recent_transcript(m.meeting_id, limit=3)
    assert [e.content for e in recent] == ["m7", "m8", "m9"]


def test_d10_audit_chain_ten_entries(temp_db: Database) -> None:
    """D10 — append_audit builds a valid hash chain across 10 entries."""
    m = _meeting(temp_db, title="x")
    base = utc_now()
    for i in range(10):
        temp_db.append_audit(
            AuditEntry(
                meeting_id=m.meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"i": i},
                timestamp=base + timedelta(seconds=i),
            )
        )
    rows = _audit_rows(temp_db, m.meeting_id)
    assert len(rows) == 10
    prev = ""
    for row in rows:
        assert row["previous_hash"] == prev
        # Recompute the hash from stored fields and confirm it matches
        rebuilt = AuditEntry(
            audit_id=row["audit_id"],
            participant_id=row["participant_id"],
            organisation_id=row["organisation_id"],
            meeting_id=row["meeting_id"],
            timestamp=row["timestamp"],
            event_type=AuditEventType(row["event_type"]),
            payload={"i": row["payload"] and __import__("json").loads(row["payload"])["i"]},
            worker_id=row["worker_id"],
            previous_hash=row["previous_hash"],
        )
        assert rebuilt.compute_hash() == row["entry_hash"]
        prev = row["entry_hash"]


def test_d11_separate_chains_per_meeting(temp_db: Database) -> None:
    """D11 — Different meetings maintain independent audit chains."""
    m1 = _meeting(temp_db, title="a")
    m2 = _meeting(temp_db, title="b")
    base = utc_now()
    for i in range(3):
        temp_db.append_audit(
            AuditEntry(
                meeting_id=m1.meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"i": i},
                timestamp=base + timedelta(seconds=i),
            )
        )
        temp_db.append_audit(
            AuditEntry(
                meeting_id=m2.meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"i": i},
                timestamp=base + timedelta(seconds=i),
            )
        )
    rows1 = _audit_rows(temp_db, m1.meeting_id)
    rows2 = _audit_rows(temp_db, m2.meeting_id)
    # Each chain starts from genesis ("") independently
    assert rows1[0]["previous_hash"] == ""
    assert rows2[0]["previous_hash"] == ""
    # Chains don't reference each other
    hashes1 = {r["entry_hash"] for r in rows1}
    for r in rows2:
        assert r["previous_hash"] not in hashes1 or r["previous_hash"] == ""


def test_d12_action_item_round_trip(temp_db: Database) -> None:
    """D12 — save_action_item and get_action_items round-trip."""
    m = _meeting(temp_db, title="x")
    item = ActionItem(
        meeting_id=m.meeting_id, owner="Sarah", task="Draft migration plan", deadline="Friday"
    )
    temp_db.save_action_item(item)
    items = temp_db.get_action_items(m.meeting_id)
    assert len(items) == 1
    assert items[0].owner == "Sarah"
    assert items[0].task == "Draft migration plan"
    assert items[0].completed is False


def test_d13_memories_ordered_by_recency(temp_db: Database) -> None:
    """D13 — save_memory and get_memories return newest first."""
    p = temp_db.get_default_participant()
    base = utc_now()
    for i in range(3):
        temp_db.save_memory(
            MemoryEntry(
                participant_id=p.participant_id,
                memory_type="fact",
                content=f"fact {i}",
                created_at=base + timedelta(seconds=i),
            )
        )
    memories = temp_db.get_memories(p.participant_id)
    assert [mem.content for mem in memories] == ["fact 2", "fact 1", "fact 0"]


def test_d14_organisation_id_persists(temp_db: Database) -> None:
    """D14 — organisation_id round-trips (multi-tenancy groundwork)."""
    p = Participant(organisation_id="acme", name="Acme Bot")
    temp_db.create_participant(p)
    assert temp_db.get_participant(p.participant_id).organisation_id == "acme"

    m = Meeting(
        organisation_id="acme", participant_id=p.participant_id, status=MeetingStatus.ACTIVE
    )
    temp_db.create_meeting(m)
    assert temp_db.get_meeting(m.meeting_id).organisation_id == "acme"


def test_d15_concurrent_writes_no_corruption(temp_db: Database) -> None:
    """D15 — Concurrent writers under WAL all land, with no corruption."""
    m = _meeting(temp_db, title="x")
    errors: list[Exception] = []
    per_thread = 20
    n_threads = 5

    def writer(tid: int) -> None:
        try:
            db = Database(temp_db.db_path)  # independent connection per thread
            base = utc_now()
            for i in range(per_thread):
                db.append_audit(
                    AuditEntry(
                        meeting_id=m.meeting_id,
                        event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                        payload={"tid": tid, "i": i},
                        timestamp=base + timedelta(microseconds=i),
                    )
                )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors}"
    rows = _audit_rows(temp_db, m.meeting_id)
    assert len(rows) == per_thread * n_threads


def test_d16_set_memory_confidence(temp_db: Database) -> None:
    """D16 — set_memory_confidence updates a stored memory (supersession, D10)."""
    p = temp_db.get_default_participant()
    mem = MemoryEntry(
        participant_id=p.participant_id,
        memory_type="fact",
        content="The API limit is 100 rps.",
        confidence=0.9,
    )
    temp_db.save_memory(mem)
    temp_db.set_memory_confidence(mem.memory_id, 0.3)
    loaded = temp_db.get_memories(p.participant_id)
    assert len(loaded) == 1
    assert loaded[0].confidence == 0.3


def test_d17_get_audit_log_filters_and_orders(temp_db: Database) -> None:
    """D17 — get_audit_log reads entries oldest first, filtered by type."""
    from src.models import AuditEntry, AuditEventType

    for i in range(3):
        temp_db.append_audit(
            AuditEntry(
                meeting_id="m1",
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={"n": i},
            )
        )
    temp_db.append_audit(
        AuditEntry(meeting_id="m1", event_type=AuditEventType.CONTRIBUTION_MADE, payload={"n": 9})
    )
    temp_db.append_audit(
        AuditEntry(meeting_id="m2", event_type=AuditEventType.CONTRIBUTION_MADE, payload={"n": 7})
    )

    assert len(temp_db.get_audit_log(meeting_id="m1")) == 4
    contributions = temp_db.get_audit_log(
        meeting_id="m1", event_type=AuditEventType.CONTRIBUTION_MADE.value
    )
    assert [e.payload["n"] for e in contributions] == [9]
    assert [e.payload["n"] for e in temp_db.get_audit_log(meeting_id="m1")][:3] == [0, 1, 2]
    assert temp_db.get_audit_log() != []


def test_d18_list_meetings_returns_newest_first(temp_db: Database) -> None:
    """D18 — list_meetings returns every meeting, newest first."""
    from datetime import timedelta

    from src.models import utc_now

    pid = temp_db.get_default_participant().participant_id
    now = utc_now()
    temp_db.create_meeting(
        Meeting(
            participant_id=pid,
            title="First",
            channel_id="C1",
            mode_config={"name": "secretary"},
            created_at=now - timedelta(minutes=5),
        )
    )
    temp_db.create_meeting(
        Meeting(
            participant_id=pid,
            title="Second",
            channel_id="C2",
            mode_config={"name": "facilitator"},
            created_at=now,
        )
    )

    titles = [m.title for m in temp_db.list_meetings()]
    assert titles[0] == "Second"
    assert set(titles) == {"First", "Second"}
    assert temp_db.list_meetings(limit=1) == temp_db.list_meetings()[:1]
