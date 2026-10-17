"""Database layer for Synedrios.

SQLite for Phase 1. All tables include organisation_id for future
multi-tenancy. The audit log table is append-only by application
convention (no UPDATE or DELETE queries are ever issued against it).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

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
)

DB_PATH = Path("data/synedrios.db")


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _adapt_datetime(dt: datetime) -> str:
    return dt.isoformat()


def _convert_datetime(val: bytes) -> datetime:
    return datetime.fromisoformat(val.decode())


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("timestamp", _convert_datetime)


SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    default_role TEXT NOT NULL DEFAULT 'secretary',
    default_tone TEXT NOT NULL DEFAULT '{}',
    api_config TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    participant_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'Untitled Meeting',
    status TEXT NOT NULL DEFAULT 'scheduled',
    mode_config TEXT NOT NULL DEFAULT '{}',
    connector_type TEXT NOT NULL DEFAULT 'slack',
    channel_id TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id)
);

CREATE TABLE IF NOT EXISTS transcript (
    event_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    speaker TEXT,
    speaker_id TEXT,
    content TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS action_items (
    action_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    owner TEXT NOT NULL,
    task TEXT NOT NULL,
    deadline TEXT,
    context TEXT NOT NULL DEFAULT '',
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    meeting_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    worker_id TEXT NOT NULL DEFAULT '',
    previous_hash TEXT NOT NULL DEFAULT '',
    entry_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    organisation_id TEXT NOT NULL DEFAULT 'default',
    meeting_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id)
);

CREATE INDEX IF NOT EXISTS idx_meetings_org ON meetings(organisation_id);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
CREATE INDEX IF NOT EXISTS idx_transcript_meeting ON transcript(meeting_id);
CREATE INDEX IF NOT EXISTS idx_audit_meeting ON audit_log(meeting_id);
CREATE INDEX IF NOT EXISTS idx_audit_participant ON audit_log(participant_id);
CREATE INDEX IF NOT EXISTS idx_actions_meeting ON action_items(meeting_id);
CREATE INDEX IF NOT EXISTS idx_actions_owner ON action_items(owner);
CREATE INDEX IF NOT EXISTS idx_memories_participant ON memories(participant_id);
"""


class Database:
    """SQLite database wrapper with all Synedrios operations."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DB_PATH
        _ensure_dir()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._tx() as conn:
            conn.executescript(SCHEMA)

    # ----- Participants -----

    def create_participant(self, p: Participant) -> Participant:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO participants
                   (participant_id, organisation_id, name, default_role,
                    default_tone, api_config, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    p.participant_id,
                    p.organisation_id,
                    p.name,
                    p.default_role,
                    json.dumps(p.default_tone),
                    json.dumps(p.api_config),
                    p.created_at,
                ),
            )
        return p

    def get_participant(self, participant_id: str) -> Optional[Participant]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE participant_id = ?",
                (participant_id,),
            ).fetchone()
        if not row:
            return None
        return Participant(
            participant_id=row["participant_id"],
            organisation_id=row["organisation_id"],
            name=row["name"],
            default_role=row["default_role"],
            default_tone=json.loads(row["default_tone"]),
            api_config=json.loads(row["api_config"]),
            created_at=row["created_at"],
        )

    def get_default_participant(self) -> Participant:
        """Get or create the default participant."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE organisation_id = 'default' LIMIT 1"
            ).fetchone()
        if row:
            return Participant(
                participant_id=row["participant_id"],
                organisation_id=row["organisation_id"],
                name=row["name"],
                default_role=row["default_role"],
                default_tone=json.loads(row["default_tone"]),
                api_config=json.loads(row["api_config"]),
                created_at=row["created_at"],
            )
        p = Participant()
        return self.create_participant(p)

    # ----- Meetings -----

    def create_meeting(self, m: Meeting) -> Meeting:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO meetings
                   (meeting_id, organisation_id, participant_id, title,
                    status, mode_config, connector_type, channel_id,
                    started_at, ended_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m.meeting_id,
                    m.organisation_id,
                    m.participant_id,
                    m.title,
                    m.status.value,
                    json.dumps(m.mode_config),
                    m.connector_type,
                    m.channel_id,
                    m.started_at,
                    m.ended_at,
                    m.created_at,
                ),
            )
        return m

    def update_meeting_status(
        self,
        meeting_id: str,
        status: MeetingStatus,
        ended_at: Optional[datetime] = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE meetings SET status = ?, ended_at = ? WHERE meeting_id = ?",
                (status.value, ended_at, meeting_id),
            )

    def get_meeting(self, meeting_id: str) -> Optional[Meeting]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM meetings WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        if not row:
            return None
        return Meeting(
            meeting_id=row["meeting_id"],
            organisation_id=row["organisation_id"],
            participant_id=row["participant_id"],
            title=row["title"],
            status=MeetingStatus(row["status"]),
            mode_config=json.loads(row["mode_config"]),
            connector_type=row["connector_type"],
            channel_id=row["channel_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            created_at=row["created_at"],
        )

    def get_active_meeting_by_channel(self, channel_id: str) -> Optional[Meeting]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM meetings WHERE channel_id = ? AND status = 'active' LIMIT 1",
                (channel_id,),
            ).fetchone()
        if not row:
            return None
        return Meeting(
            meeting_id=row["meeting_id"],
            organisation_id=row["organisation_id"],
            participant_id=row["participant_id"],
            title=row["title"],
            status=MeetingStatus(row["status"]),
            mode_config=json.loads(row["mode_config"]),
            connector_type=row["connector_type"],
            channel_id=row["channel_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            created_at=row["created_at"],
        )

    def list_meetings(self, limit: int = 100) -> list[Meeting]:
        """All meetings, newest first. Used by the calibration tooling."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM meetings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            Meeting(
                meeting_id=row["meeting_id"],
                organisation_id=row["organisation_id"],
                participant_id=row["participant_id"],
                title=row["title"],
                status=MeetingStatus(row["status"]),
                mode_config=json.loads(row["mode_config"]),
                connector_type=row["connector_type"],
                channel_id=row["channel_id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ----- Transcript -----

    def save_event(self, meeting_id: str, event: MeetingEvent) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO transcript
                   (event_id, meeting_id, organisation_id, timestamp,
                    event_type, speaker, speaker_id, content,
                    confidence, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    meeting_id,
                    "default",
                    event.timestamp,
                    event.event_type.value,
                    event.speaker,
                    event.speaker_id,
                    event.content,
                    event.confidence,
                    json.dumps(event.metadata),
                ),
            )

    def get_transcript(self, meeting_id: str, limit: int = 500) -> list[MeetingEvent]:
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT * FROM transcript
                   WHERE meeting_id = ?
                   ORDER BY timestamp ASC LIMIT ?""",
                (meeting_id, limit),
            ).fetchall()
        return [
            MeetingEvent(
                event_id=r["event_id"],
                timestamp=r["timestamp"],
                event_type=EventType(r["event_type"])
                if r["event_type"] in EventType.__members__.values()
                else EventType.CHAT,
                speaker=r["speaker"],
                speaker_id=r["speaker_id"],
                content=r["content"],
                confidence=r["confidence"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]

    def get_recent_transcript(self, meeting_id: str, limit: int = 50) -> list[MeetingEvent]:
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT * FROM transcript
                   WHERE meeting_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (meeting_id, limit),
            ).fetchall()
        events = [
            MeetingEvent(
                event_id=r["event_id"],
                timestamp=r["timestamp"],
                event_type=EventType(r["event_type"])
                if r["event_type"] in EventType.__members__.values()
                else EventType.CHAT,
                speaker=r["speaker"],
                speaker_id=r["speaker_id"],
                content=r["content"],
                confidence=r["confidence"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]
        events.reverse()
        return events

    # ----- Action items -----

    def save_action_item(self, item: ActionItem) -> ActionItem:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO action_items
                   (action_id, meeting_id, organisation_id, owner,
                    task, deadline, context, completed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.action_id,
                    item.meeting_id,
                    item.organisation_id,
                    item.owner,
                    item.task,
                    item.deadline,
                    item.context,
                    int(item.completed),
                    item.created_at,
                ),
            )
        return item

    def get_action_items(self, meeting_id: str) -> list[ActionItem]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM action_items WHERE meeting_id = ? ORDER BY created_at",
                (meeting_id,),
            ).fetchall()
        return [
            ActionItem(
                action_id=r["action_id"],
                meeting_id=r["meeting_id"],
                organisation_id=r["organisation_id"],
                owner=r["owner"],
                task=r["task"],
                deadline=r["deadline"],
                context=r["context"],
                completed=bool(r["completed"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ----- Audit log -----

    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        """Append an entry to the audit log. Never update or delete."""
        with self._tx() as conn:
            # Get the hash of the previous entry for chain integrity
            prev = conn.execute(
                """SELECT entry_hash FROM audit_log
                   WHERE meeting_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (entry.meeting_id,),
            ).fetchone()
            prev_hash = prev["entry_hash"] if prev else ""
            entry.finalise(prev_hash)

            conn.execute(
                """INSERT INTO audit_log
                   (audit_id, participant_id, organisation_id, meeting_id,
                    timestamp, event_type, payload, worker_id,
                    previous_hash, entry_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.audit_id,
                    entry.participant_id,
                    entry.organisation_id,
                    entry.meeting_id,
                    entry.timestamp,
                    entry.event_type.value,
                    json.dumps(entry.payload),
                    entry.worker_id,
                    entry.previous_hash,
                    entry.entry_hash,
                ),
            )
        return entry

    def get_audit_log(
        self,
        meeting_id: str = "",
        event_type: str = "",
        limit: int = 10000,
    ) -> list[AuditEntry]:
        """Read audit entries, oldest first, optionally filtered.

        Read-only — the audit log stays append-only. Used to reconstruct
        turn-management state after a worker restart (architecture principle 1)
        and by the calibration tooling to analyse real-meeting behaviour.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if meeting_id:
            clauses.append("meeting_id = ?")
            params.append(meeting_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY timestamp, rowid LIMIT ?",
                tuple(params),
            ).fetchall()
        return [
            AuditEntry(
                audit_id=row["audit_id"],
                participant_id=row["participant_id"],
                organisation_id=row["organisation_id"],
                meeting_id=row["meeting_id"],
                timestamp=row["timestamp"],
                event_type=AuditEventType(row["event_type"]),
                payload=json.loads(row["payload"]),
                worker_id=row["worker_id"],
                previous_hash=row["previous_hash"],
                entry_hash=row["entry_hash"],
            )
            for row in rows
        ]

    # ----- Memories -----

    def save_memory(self, mem: MemoryEntry) -> MemoryEntry:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO memories
                   (memory_id, participant_id, organisation_id, meeting_id,
                    memory_type, content, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mem.memory_id,
                    mem.participant_id,
                    mem.organisation_id,
                    mem.meeting_id,
                    mem.memory_type,
                    mem.content,
                    mem.confidence,
                    mem.created_at,
                ),
            )
        return mem

    def set_memory_confidence(self, memory_id: str, confidence: float) -> None:
        """Update a memory's confidence (used when a newer memory supersedes it).

        The memories table is mutable — only audit_log is append-only — so
        lowering a superseded memory's confidence is permitted (D10).
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE memories SET confidence = ? WHERE memory_id = ?",
                (confidence, memory_id),
            )

    def get_memories(self, participant_id: str, limit: int = 100) -> list[MemoryEntry]:
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE participant_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (participant_id, limit),
            ).fetchall()
        return [
            MemoryEntry(
                memory_id=r["memory_id"],
                participant_id=r["participant_id"],
                organisation_id=r["organisation_id"],
                meeting_id=r["meeting_id"],
                memory_type=r["memory_type"],
                content=r["content"],
                confidence=r["confidence"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
