"""Core data models for Synedrios.

All persistent entities are defined here. The database schema is derived
from these models. Every model includes organisation_id for multi-tenancy
from day one, even though Phase 1 uses a single default organisation.
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def new_id() -> str:
    """Generate a new UUID4 string identifier."""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Current UTC timestamp."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InitiativeLevel(int, enum.Enum):
    """How proactively the AI participates. See participation model docs."""

    SILENT = 0
    RESPOND_ONLY = 1
    FLAG_AND_WAIT = 2
    SCHEDULED_TURNS = 3
    REACTIVE = 4
    ACTIVE_PARTICIPANT = 5
    LEAD = 6


class AuthorityLevel(int, enum.Enum):
    """How assertively the AI frames its contributions."""

    OBSERVER = 1
    ADVISOR = 2
    PEER = 3
    EXPERT = 4
    CHAIR = 5


class MeetingStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    ENDED = "ended"


class EventType(str, enum.Enum):
    """Types of events in the meeting event stream."""

    SPEECH = "speech"
    CHAT = "chat"
    SLIDE_CHANGE = "slide_change"
    JOIN = "join"
    LEAVE = "leave"
    SYSTEM = "system"


class AuditEventType(str, enum.Enum):
    """Types of entries in the immutable audit log."""

    TRANSCRIPT_SEGMENT = "transcript_segment"
    TRIGGER_FIRED = "trigger_fired"
    TRIGGER_SUPPRESSED = "trigger_suppressed"
    CONTRIBUTION_MADE = "contribution_made"
    CONTRIBUTION_WITHHELD = "contribution_withheld"
    ALERT_SENT = "alert_sent"
    ALERT_RECEIVED = "alert_received"
    DECISION_CAPTURED = "decision_captured"
    ACTION_ITEM_CAPTURED = "action_item_captured"
    POST_MEETING_OUTPUT = "post_meeting_output"
    CONFIG_CHANGE = "config_change"
    ERROR = "error"
    MEETING_STARTED = "meeting_started"
    MEETING_ENDED = "meeting_ended"
    MEMORY_CREATED = "memory_created"
    MEMORY_EXTRACTION_FAILED = "memory_extraction_failed"


# ---------------------------------------------------------------------------
# Meeting event (the normalised input stream)
# ---------------------------------------------------------------------------


class MeetingEvent(BaseModel):
    """A single event in the meeting stream.

    All input (chat messages, transcribed speech, slide changes) is
    normalised into this format before reaching the participation engine.
    """

    event_id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: EventType
    speaker: Optional[str] = None
    speaker_id: Optional[str] = None
    content: str = ""
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


class TriggerResult(BaseModel):
    """Result of evaluating a trigger against the current meeting state."""

    trigger_type: str
    fired: bool
    confidence: float = 1.0
    priority: int = 99
    reason: str = ""
    source_event_id: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Participant (the AI avatar identity)
# ---------------------------------------------------------------------------


class Participant(BaseModel):
    """A persistent AI participant identity.

    This is the avatar. It survives across meetings. Its memory accumulates.
    Any worker can load this from the database to handle a meeting.
    """

    participant_id: str = Field(default_factory=new_id)
    organisation_id: str = "default"
    name: str = "Synedrios"
    default_role: str = "secretary"
    default_tone: dict[str, Any] = Field(
        default_factory=lambda: {
            "register": 2,
            "directness": 2,
            "warmth": 2,
            "hedging": 2,
            "hierarchy_awareness": 1,
        }
    )
    api_config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Meeting
# ---------------------------------------------------------------------------


class Meeting(BaseModel):
    """A single meeting session."""

    meeting_id: str = Field(default_factory=new_id)
    organisation_id: str = "default"
    participant_id: str = ""
    title: str = "Untitled Meeting"
    status: MeetingStatus = MeetingStatus.SCHEDULED
    mode_config: dict[str, Any] = Field(default_factory=dict)
    connector_type: str = "slack"
    channel_id: str = ""
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Action item
# ---------------------------------------------------------------------------


class ActionItem(BaseModel):
    """A task extracted from the meeting."""

    action_id: str = Field(default_factory=new_id)
    meeting_id: str = ""
    organisation_id: str = "default"
    owner: str = ""
    task: str = ""
    deadline: Optional[str] = None
    context: str = ""
    completed: bool = False
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Audit log entry
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """An immutable entry in the audit log.

    Append-only. Each entry includes the hash of the previous entry,
    forming a tamper-evident hash chain.
    """

    audit_id: str = Field(default_factory=new_id)
    participant_id: str = ""
    organisation_id: str = "default"
    meeting_id: str = ""
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: AuditEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    worker_id: str = ""
    previous_hash: str = ""
    entry_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this entry for chain integrity."""
        content = json.dumps(
            {
                "audit_id": self.audit_id,
                "participant_id": self.participant_id,
                "organisation_id": self.organisation_id,
                "meeting_id": self.meeting_id,
                "timestamp": self.timestamp.isoformat(),
                "event_type": self.event_type.value,
                "payload": self.payload,
                "worker_id": self.worker_id,
                "previous_hash": self.previous_hash,
            },
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def finalise(self, previous_hash: str = "") -> AuditEntry:
        """Set the previous hash and compute entry hash."""
        self.previous_hash = previous_hash
        self.entry_hash = self.compute_hash()
        return self


# ---------------------------------------------------------------------------
# Memory entry
# ---------------------------------------------------------------------------


class MemoryEntry(BaseModel):
    """A piece of knowledge the participant has learned across meetings."""

    memory_id: str = Field(default_factory=new_id)
    participant_id: str = ""
    organisation_id: str = "default"
    meeting_id: str = ""
    memory_type: str = ""  # "person", "decision", "fact", "preference", "relationship"
    content: str = ""
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=utc_now)


# The valid memory types (see MemoryEntry.memory_type). Kept here so both the
# extraction engine and any validation share one definition.
MEMORY_TYPES = frozenset(
    {
        "person",
        "decision",
        "fact",
        "preference",
        "relationship",
    }
)


class ExtractedMemory(BaseModel):
    """A memory candidate produced by the post-meeting extraction pass.

    Transfer object between the participation engine (which runs the LLM
    extraction) and the connector (which persists rows and audits). Not stored
    directly — it becomes a MemoryEntry once the connector attaches the
    participant, organisation, and meeting ids.
    """

    memory_type: str
    content: str
    confidence: float = 0.8
    status: str = "new"  # "new" | "update" (an update supersedes an older memory)
    supersedes: str = ""  # exact content of the superseded memory, if status == "update"
