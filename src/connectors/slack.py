"""Slack connector for Synedrios.

Handles the Slack bot integration: listens to channel messages,
manages meeting lifecycle (start/end), feeds events to the
participation engine, and posts AI contributions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import timezone
from typing import Any, Optional

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from src.engine.modes import ModeConfig
from src.engine.participation import ParticipationEngine
from src.llm.adapter import LLMAdapter
from src.models import (
    AuditEntry,
    AuditEventType,
    EventType,
    Meeting,
    MeetingEvent,
    MeetingStatus,
    MemoryEntry,
    utc_now,
)
from src.persistence.database import Database

logger = logging.getLogger(__name__)


class SlackConnector:
    """Slack bot connector for text-based meetings.

    Commands (issued in a Slack channel):
        @synedrios start meeting "Title" as <role>
        @synedrios end meeting
        @synedrios status
        @synedrios help

    During an active meeting, all channel messages are captured
    as meeting events and evaluated by the participation engine.
    """

    def __init__(
        self,
        db: Database,
        llm: LLMAdapter,
        modes: dict[str, ModeConfig],
        participant_name: str = "Synedrios",
    ) -> None:
        self.db = db
        self.llm = llm
        self.modes = modes
        self.participant_name = participant_name

        # Active meeting sessions: channel_id -> engine. This is a cache, not
        # the source of truth: a session absent here is rebuilt from the
        # database by _get_session (architecture principle 1 — stateless
        # workers, stateful database).
        self._sessions: dict[str, MeetingSession] = {}

        # The bot's own Slack user id, resolved lazily. Used to recognise
        # messages that Slack also delivers as app_mention events.
        self._bot_user_id: str = ""

        # Slack user id -> display name, resolved once per user. Message events
        # do not reliably carry a user profile, and a transcript of raw ids
        # produces notes that assign every action item to "U08KX3L4Q2".
        self._user_names: dict[str, str] = {}

        # Get or create the default participant
        self._participant = db.get_default_participant()
        self._participant.name = participant_name

        # Slack app setup
        self.app = AsyncApp(
            token=os.environ.get("SLACK_BOT_TOKEN", ""),
            name="Synedrios",
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Slack event handlers."""

        @self.app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            await self._handle_mention(event, say)

        @self.app.event("message")
        async def handle_message(event: dict, say: Any, context: Any = None) -> None:
            await self._handle_message(event, say, context)

    async def _handle_mention(self, event: dict, say: Any) -> None:
        """Handle @synedrios mentions — commands."""
        text = event.get("text", "")
        channel = event.get("channel", "")
        user = event.get("user", "")

        # Strip the bot mention. Command matching is case-insensitive, but the
        # original casing is preserved and handed to _cmd_start so a quoted
        # meeting title survives verbatim.
        raw = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        command = raw.lower()

        if command.startswith("start"):
            await self._cmd_start(raw, channel, user, say)
        elif command.startswith("end"):
            await self._cmd_end(channel, user, say)
        elif command.startswith("status"):
            await self._cmd_status(channel, say)
        elif command.startswith("help"):
            await self._cmd_help(say)
        else:
            # Treat as a direct address during a meeting
            if await self._get_session(channel):
                await self._process_meeting_event(event, say, addressed=True)
            else:
                await say(
                    f"No active meeting in this channel. "
                    f"Start one with: `@{self.participant_name} start meeting "
                    f'"Meeting Title" as secretary`'
                )

    async def _handle_message(self, event: dict, say: Any, context: Any = None) -> None:
        """Handle regular channel messages during an active meeting."""
        channel = event.get("channel", "")
        subtype = event.get("subtype", "")

        # Ignore bot messages, edits, etc.
        if subtype or event.get("bot_id"):
            return

        # Slack delivers a message that mentions the bot TWICE — once as
        # `message` and once as `app_mention`. Without this guard the same
        # message would be recorded in the transcript twice and could draw two
        # replies. The mention path handles it; drop it here.
        bot_id = await self._resolve_bot_user_id(context)
        if bot_id and f"<@{bot_id}>" in event.get("text", ""):
            return

        if await self._get_session(channel):
            await self._process_meeting_event(event, say, addressed=False)

    async def _resolve_bot_user_id(self, context: Any = None) -> str:
        """Return the bot's own Slack user id, resolving and caching it once.

        Prefers the value Bolt puts on the request context; falls back to
        `auth.test`. Returns "" if neither is available, in which case the
        mention de-duplication guard simply does not engage.
        """
        if self._bot_user_id:
            return self._bot_user_id

        if context is not None:
            candidate = ""
            try:
                candidate = context.get("bot_user_id") or ""
            except AttributeError:
                candidate = getattr(context, "bot_user_id", "") or ""
            if candidate:
                self._bot_user_id = candidate
                return self._bot_user_id

        try:
            response = await self.app.client.auth_test()
            self._bot_user_id = response.get("user_id", "") or ""
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning("Could not resolve bot user id via auth.test: %s", exc)
        return self._bot_user_id

    async def _resolve_speaker(self, user_id: str, event: dict) -> str:
        """The best available human name for a Slack user id.

        Prefers the profile Slack attached to the event, falls back to a cached
        `users.info` lookup, and finally to the raw id. Names matter more here
        than they look: the transcript is what every post-meeting document is
        written from, so an unresolved id propagates into the notes, the
        summary, and every action item's owner.
        """
        if not user_id:
            return "Unknown"

        profile = event.get("user_profile") or {}
        name = profile.get("display_name") or profile.get("real_name") or ""
        if name:
            self._user_names[user_id] = name
            return name
        return await self._lookup_user_name(user_id)

    async def _lookup_user_name(self, user_id: str) -> str:
        """Look up a display name by user id alone, cached, falling back to the id.

        Used for people *mentioned* in a message as well as the sender, so it
        cannot consult the event's own user profile.
        """
        if not user_id:
            return "Unknown"
        cached = self._user_names.get(user_id)
        if cached:
            return cached

        name = ""
        try:
            info = await self.app.client.users_info(user=user_id)
            user: dict[str, Any] = info.get("user", {}) or {}
            user_profile = user.get("profile", {}) or {}
            name = (
                user_profile.get("display_name")
                or user_profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
                or ""
            )
        except Exception as exc:
            logger.warning("Could not resolve a display name for %s: %s", user_id, exc)

        name = name or user_id
        self._user_names[user_id] = name
        return name

    async def _render_mentions(self, text: str) -> tuple[str, list[str]]:
        """Replace `<@Uxxx>` markup with display names; report who was addressed.

        Returns the readable text and the names of everyone mentioned other than
        the AI itself. Two things depend on this. The transcript is what every
        post-meeting document is written from, so "Sarah, can you check the
        migration?" reads better there than the same line with the mention
        deleted. And the participation engine needs to know that the question
        had a name on it — a question put to a colleague is not the AI's to
        answer, and once the markup is stripped the two are indistinguishable.
        """
        mention_ids = re.findall(r"<@([A-Z0-9]+)>", text)
        if not mention_ids:
            return text.strip(), []

        directed_at: list[str] = []
        for user_id in dict.fromkeys(mention_ids):  # de-duplicated, order kept
            if user_id == self._bot_user_id:
                text = text.replace(f"<@{user_id}>", " ")
                continue
            name = await self._lookup_user_name(user_id)
            text = text.replace(f"<@{user_id}>", name)
            if name not in directed_at:
                directed_at.append(name)

        return re.sub(r"\s{2,}", " ", text).strip(), directed_at

    async def _get_session(self, channel: str) -> Optional[MeetingSession]:
        """Return the live session for a channel, rebuilding it if necessary.

        The in-process session dict is only a cache. If this worker did not
        start the meeting — a restart, a redeploy, or a different worker — the
        session is reconstructed from the database so the meeting continues
        uninterrupted (architecture principle 1).
        """
        session = self._sessions.get(channel)
        if session:
            return session

        meeting = self.db.get_active_meeting_by_channel(channel)
        if not meeting:
            return None

        try:
            mode = ModeConfig(**meeting.mode_config)
        except Exception as exc:
            logger.error("Cannot rebuild mode config for meeting %s: %s", meeting.meeting_id, exc)
            return None

        engine = ParticipationEngine(
            mode=mode,
            llm=self.llm,
            participant_name=self.participant_name,
            participant_id=self._participant.participant_id,
            meeting_id=meeting.meeting_id,
        )
        engine.injected_memories = self._memories_for_injection()
        self._restore_turn_state(engine, meeting.meeting_id)

        session = MeetingSession(meeting=meeting, engine=engine, channel_id=channel)
        self._sessions[channel] = session
        logger.info(
            "Rehydrated active meeting %s (role=%s) in channel %s from the database",
            meeting.title,
            mode.name,
            channel,
        )
        return session

    def _memories_for_injection(self) -> list[str]:
        """Cross-meeting memories to inject into a session's system prompt (D10).

        Superseded / low-confidence memories are filtered out; newest first,
        capped so injected context stays focused.
        """
        stored = self.db.get_memories(self._participant.participant_id)
        return [f"[{m.memory_type}] {m.content}" for m in stored if m.confidence >= 0.4][:10]

    def _restore_turn_state(self, engine: ParticipationEngine, meeting_id: str) -> None:
        """Rebuild cooldown / rate-limit state from the audit log.

        Turn management must survive a restart, otherwise a rehydrated meeting
        would let the AI speak immediately regardless of its cooldown. The
        audit log is the record of every contribution made, so it is the
        authoritative source for that state.
        """
        now_wall = utc_now()
        now_mono = time.time()
        timestamps: list[float] = []
        messages_seen = 0
        last_contribution_at: dict[str, int] = {}

        # One pass over the meeting's audit entries in order. Counting the
        # transcript segments as we go reproduces the engine's message clock,
        # so a rehydrated meeting resumes with the same refractory state it had
        # before the restart rather than a clean slate.
        for entry in self.db.get_audit_log(meeting_id=meeting_id):
            if entry.event_type == AuditEventType.TRANSCRIPT_SEGMENT:
                # Directly-addressed messages bypass evaluate_triggers live,
                # so they never advance the engine's refractory clock — a
                # faithful rebuild must skip them too, or a restart would
                # inflate the clock and let a state trigger fire early.
                if entry.payload.get("addressed"):
                    continue
                messages_seen += 1
            elif entry.event_type == AuditEventType.CONTRIBUTION_MADE:
                stamp = entry.timestamp
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age = (now_wall - stamp).total_seconds()
                timestamps.append(now_mono - max(age, 0.0))
                trigger = str(entry.payload.get("trigger", ""))
                if trigger:
                    last_contribution_at[trigger] = messages_seen

        engine._messages_seen = messages_seen
        engine._last_contribution_at = last_contribution_at
        if not timestamps:
            return
        engine._contribution_count = len(timestamps)
        engine._contributions_window = timestamps[-20:]
        engine._last_contribution_time = timestamps[-1]

    async def _cmd_start(self, text: str, channel: str, user: str, say: Any) -> None:
        """Start a new meeting in this channel.

        `text` is the command with the bot mention stripped, in its original
        casing — a quoted meeting title is recorded exactly as it was typed.
        """
        if await self._get_session(channel):
            await say("A meeting is already active in this channel. End it first.")
            return

        # Parse: start meeting "Title" as <role>
        title = "Untitled Meeting"
        role_name = "secretary"

        title_match = re.search(r'"([^"]+)"', text)
        if title_match:
            title = title_match.group(1).strip() or title

        role_match = re.search(r"\bas\s+(\w+)", text, re.IGNORECASE)
        if role_match:
            role_name = role_match.group(1).lower()

        if role_name not in self.modes:
            available = ", ".join(sorted(self.modes.keys()))
            await say(f"Unknown role: `{role_name}`. Available roles: {available}")
            return

        mode = self.modes[role_name]

        # Create meeting in database
        meeting = Meeting(
            organisation_id=self._participant.organisation_id,
            participant_id=self._participant.participant_id,
            title=title,
            status=MeetingStatus.ACTIVE,
            mode_config=mode.model_dump(),
            connector_type="slack",
            channel_id=channel,
            started_at=utc_now(),
        )
        self.db.create_meeting(meeting)

        # Create participation engine
        engine = ParticipationEngine(
            mode=mode,
            llm=self.llm,
            participant_name=self.participant_name,
            participant_id=self._participant.participant_id,
            meeting_id=meeting.meeting_id,
        )

        # Inject accumulated cross-meeting memory (D10): the avatar recalls what
        # it learned in earlier meetings.
        engine.injected_memories = self._memories_for_injection()

        session = MeetingSession(
            meeting=meeting,
            engine=engine,
            channel_id=channel,
        )
        self._sessions[channel] = session

        # Audit log
        self.db.append_audit(
            AuditEntry(
                participant_id=self._participant.participant_id,
                meeting_id=meeting.meeting_id,
                event_type=AuditEventType.MEETING_STARTED,
                payload={
                    "title": title,
                    "role": role_name,
                    "channel": channel,
                    "started_by": user,
                },
            )
        )

        # Post introduction
        intro = engine.get_introduction()
        await say(f"📋 *Meeting started: {title}*\n\n{intro}")

        logger.info(
            "Meeting started: %s (role=%s, channel=%s)",
            title,
            role_name,
            channel,
        )

    async def _cmd_end(self, channel: str, user: str, say: Any) -> None:
        """End the active meeting and generate post-meeting outputs."""
        session = await self._get_session(channel)
        if not session:
            await say("No active meeting in this channel.")
            return

        meeting = session.meeting
        engine = session.engine

        await say("📝 Meeting ended. Generating post-meeting outputs...")

        # Update meeting status
        ended_at = utc_now()
        self.db.update_meeting_status(meeting.meeting_id, MeetingStatus.ENDED, ended_at)

        # Audit log
        self.db.append_audit(
            AuditEntry(
                participant_id=self._participant.participant_id,
                meeting_id=meeting.meeting_id,
                event_type=AuditEventType.MEETING_ENDED,
                payload={"ended_by": user, "ended_at": ended_at.isoformat()},
            )
        )

        # Generate post-meeting outputs
        transcript = self.db.get_transcript(meeting.meeting_id)

        if transcript:
            try:
                outputs = await engine.generate_post_meeting(
                    transcript, meeting.title, style="slack"
                )

                for output_type, content in outputs.items():
                    header = output_type.replace("_", " ").title()
                    # Slack has a 4000 char limit per message
                    chunks = _chunk_text(content, limit=3800)
                    if len(chunks) > 1:
                        for i, chunk in enumerate(chunks):
                            await say(f"*{header} ({i + 1}/{len(chunks)}):*\n\n{chunk}")
                    else:
                        await say(f"*{header}:*\n\n{content}")

                    # Audit log
                    self.db.append_audit(
                        AuditEntry(
                            participant_id=self._participant.participant_id,
                            meeting_id=meeting.meeting_id,
                            event_type=AuditEventType.POST_MEETING_OUTPUT,
                            payload={
                                "output_type": output_type,
                                "length": len(content),
                            },
                        )
                    )
            except Exception as exc:
                logger.error("Post-meeting generation failed: %s", exc)
                await say(f"⚠️ Error generating post-meeting outputs: {exc}")

            # Memory-writing pass (D10): extract durable knowledge for future
            # meetings, deduplicated against what the avatar already knows. A
            # failure here must never break meeting end.
            try:
                await self._extract_and_store_memories(meeting, engine, transcript, say)
            except Exception as exc:
                # Never break meeting end — but never be invisible either: in
                # the live rehearsal a (likely truncation-induced) extraction
                # failure silently cost every memory from the richest meeting.
                logger.error("Memory extraction failed: %s", exc)
                self.db.append_audit(
                    AuditEntry(
                        participant_id=self._participant.participant_id,
                        meeting_id=meeting.meeting_id,
                        event_type=AuditEventType.MEMORY_EXTRACTION_FAILED,
                        payload={"error": str(exc)[:300]},
                    )
                )
        else:
            await say("No transcript recorded — nothing to process.")

        # Clean up session
        self._sessions.pop(channel, None)
        logger.info("Meeting ended: %s", meeting.title)

    async def _extract_and_store_memories(
        self,
        meeting: Meeting,
        engine: ParticipationEngine,
        transcript: list[MeetingEvent],
        say: Any,
    ) -> None:
        """Extract, deduplicate, persist, and audit post-meeting memories (D10)."""
        existing = self.db.get_memories(self._participant.participant_id)
        content_to_id = {m.content: m.memory_id for m in existing}

        extracted = await engine.extract_memories(transcript, [m.content for m in existing])
        if not extracted:
            return

        saved = 0
        for em in extracted:
            superseded_id = ""
            if em.status == "update" and em.supersedes in content_to_id:
                # Lower the superseded memory's confidence rather than deleting
                # it — the older belief is retained but demoted (D10).
                superseded_id = content_to_id[em.supersedes]
                self.db.set_memory_confidence(superseded_id, 0.3)

            mem = MemoryEntry(
                participant_id=self._participant.participant_id,
                organisation_id=self._participant.organisation_id,
                meeting_id=meeting.meeting_id,
                memory_type=em.memory_type,
                content=em.content,
                confidence=em.confidence,
            )
            self.db.save_memory(mem)
            self.db.append_audit(
                AuditEntry(
                    participant_id=self._participant.participant_id,
                    meeting_id=meeting.meeting_id,
                    event_type=AuditEventType.MEMORY_CREATED,
                    payload={
                        "memory_id": mem.memory_id,
                        "memory_type": em.memory_type,
                        "content": em.content[:200],
                        "confidence": em.confidence,
                        "supersedes": superseded_id,
                    },
                )
            )
            saved += 1

        if saved:
            await say(f"🧠 Learned {saved} thing{'s' if saved != 1 else ''} for next time.")

    async def _cmd_status(self, channel: str, say: Any) -> None:
        """Report current meeting status."""
        session = await self._get_session(channel)
        if not session:
            await say("No active meeting in this channel.")
            return

        meeting = session.meeting
        engine = session.engine
        transcript = self.db.get_transcript(meeting.meeting_id)

        duration = ""
        if meeting.started_at:
            elapsed = utc_now() - meeting.started_at
            minutes = int(elapsed.total_seconds() / 60)
            duration = f"{minutes} minutes"

        await say(
            f"*Active meeting:* {meeting.title}\n"
            f"*Role:* {engine.mode.name}\n"
            f"*Duration:* {duration}\n"
            f"*Messages recorded:* {len(transcript)}\n"
            f"*AI contributions:* {engine._contribution_count}"
        )

    async def _cmd_help(self, say: Any) -> None:
        """Show available commands."""
        roles = ", ".join(sorted(self.modes.keys()))
        await say(
            f"*{self.participant_name} Commands:*\n\n"
            f'`@{self.participant_name} start meeting "Title" as <role>` — '
            f"Start a meeting\n"
            f"`@{self.participant_name} end meeting` — End and generate outputs\n"
            f"`@{self.participant_name} status` — Show meeting status\n"
            f"`@{self.participant_name} help` — Show this help\n\n"
            f"*Available roles:* {roles}\n\n"
            f"During a meeting, address me by name to ask a question or "
            f"request input."
        )

    async def _process_meeting_event(self, event: dict, say: Any, addressed: bool) -> None:
        """Process a message during an active meeting."""
        channel = event.get("channel", "")
        session = await self._get_session(channel)
        if not session:
            return

        meeting = session.meeting
        engine = session.engine

        # Resolve user name
        user_id = event.get("user", "")
        speaker = await self._resolve_speaker(user_id, event)
        text = event.get("text", "")

        # Render mentions as names and note who the message was aimed at. The
        # AI's own mention is removed — being addressed is already carried by
        # the `addressed` flag.
        text, directed_at = await self._render_mentions(text)
        if not text:
            return

        # Create meeting event
        meeting_event = MeetingEvent(
            event_type=EventType.CHAT,
            speaker=speaker,
            speaker_id=user_id,
            content=text,
            metadata={"directed_at": directed_at} if directed_at else {},
        )

        # Save to transcript
        self.db.save_event(meeting.meeting_id, meeting_event)

        # Audit log
        self.db.append_audit(
            AuditEntry(
                participant_id=self._participant.participant_id,
                meeting_id=meeting.meeting_id,
                event_type=AuditEventType.TRANSCRIPT_SEGMENT,
                payload={
                    "speaker": speaker,
                    "content": text[:200],  # truncate for audit
                    "addressed": addressed,
                },
            )
        )

        # Evaluate triggers
        transcript = self.db.get_recent_transcript(meeting.meeting_id)

        if addressed:
            # Direct address always triggers a response
            trigger = TriggerResult(
                trigger_type="addressed_directly",
                fired=True,
                confidence=1.0,
                priority=1,
                reason=f"Directly addressed by {speaker}",
                source_event_id=meeting_event.event_id,
            )
            triggers = [trigger]
        else:
            triggers = engine.evaluate_triggers(meeting_event, transcript)
            # LLM-based detection (factual_error, contradiction). May call the
            # LLM, gated by a heuristic pre-filter. Fired detections join the
            # trigger list; below-threshold detections are logged as suppressed
            # so the confidence threshold can be calibrated against real data (D11).
            for detection in await engine.evaluate_llm_triggers(meeting_event, transcript):
                if detection.fired:
                    triggers.append(detection)
                elif detection.context.get("detector_failed"):
                    # A detector that crashed or returned unparseable output
                    # is not a quiet meeting — record it as its own reason so
                    # calibration can see detection health (principle 4:
                    # every trigger evaluation is logged).
                    self.db.append_audit(
                        AuditEntry(
                            participant_id=self._participant.participant_id,
                            meeting_id=meeting.meeting_id,
                            event_type=AuditEventType.TRIGGER_SUPPRESSED,
                            payload={
                                "trigger": detection.trigger_type,
                                "reason": "detector_failed",
                                "detail": str(detection.context.get("raw_head", ""))[:200],
                            },
                        )
                    )
                else:
                    self.db.append_audit(
                        AuditEntry(
                            participant_id=self._participant.participant_id,
                            meeting_id=meeting.meeting_id,
                            event_type=AuditEventType.TRIGGER_SUPPRESSED,
                            payload={
                                "trigger": detection.trigger_type,
                                "reason": "below_threshold",
                                "confidence": detection.confidence,
                                "detail": detection.reason[:200],
                            },
                        )
                    )
            triggers.sort(key=lambda t: t.priority)

        if not triggers:
            return

        # Check turn management
        if not engine.can_contribute():
            # Record EVERYTHING the throttle silenced, with confidences and
            # reasons — in the first live rehearsal, 9 of these entries hid
            # whether the detectors had caught the planted errors at all
            # (they carried only a bare trigger name). Calibration needs to
            # tell "the useful ones were blocked" from "nothing was found".
            self.db.append_audit(
                AuditEntry(
                    participant_id=self._participant.participant_id,
                    meeting_id=meeting.meeting_id,
                    event_type=AuditEventType.TRIGGER_SUPPRESSED,
                    payload={
                        "trigger": triggers[0].trigger_type,
                        "reason": "cooldown_or_rate_limit",
                        "confidence": triggers[0].confidence,
                        "detail": triggers[0].reason[:200],
                        "all_triggers": [
                            {
                                "type": t.trigger_type,
                                "confidence": t.confidence,
                                "reason": t.reason[:120],
                            }
                            for t in triggers[:5]
                        ],
                    },
                )
            )
            return

        # Generate and post response
        best_trigger = triggers[0]
        try:
            response = await engine.generate_response(
                trigger=best_trigger,
                event=meeting_event,
                transcript=transcript,
            )

            if response:
                await say(response)
                engine.record_contribution(best_trigger.trigger_type)

                # The AI's words are part of the meeting record. Without this,
                # post-meeting documents are written from a transcript in
                # which the AI never spoke — in the live rehearsal the
                # advisor's own action-items document declared "No AI
                # participant was present" after it had spoken five times,
                # and the facilitator credited its own proposal to a human.
                self.db.save_event(
                    meeting.meeting_id,
                    MeetingEvent(
                        event_type=EventType.CHAT,
                        speaker=self.participant_name,
                        speaker_id=f"ai:{self._participant.participant_id}",
                        content=response,
                        metadata={"ai_contribution": True},
                    ),
                )

                # Audit log — including every OTHER trigger that fired on
                # this message: an outranked LLM detection is real evidence
                # (non-deterministic, unreconstructible by replay) and must
                # not vanish just because a higher-priority trigger won.
                payload = {
                    "trigger": best_trigger.trigger_type,
                    "trigger_reason": best_trigger.reason,
                    "response_length": len(response),
                    "response_preview": response[:200],
                }
                if len(triggers) > 1:
                    payload["all_triggers"] = [
                        {
                            "type": t.trigger_type,
                            "confidence": t.confidence,
                            "reason": t.reason[:120],
                        }
                        for t in triggers[:5]
                    ]
                self.db.append_audit(
                    AuditEntry(
                        participant_id=self._participant.participant_id,
                        meeting_id=meeting.meeting_id,
                        event_type=AuditEventType.CONTRIBUTION_MADE,
                        payload=payload,
                    )
                )
            else:
                # A fired trigger whose generation came back empty is a
                # suppressed contribution and belongs in the legal record —
                # the audit principle admits no silent path. (An empty reply
                # is a real failure mode: a truncated or refused generation.)
                self.db.append_audit(
                    AuditEntry(
                        participant_id=self._participant.participant_id,
                        meeting_id=meeting.meeting_id,
                        event_type=AuditEventType.CONTRIBUTION_WITHHELD,
                        payload={
                            "trigger": best_trigger.trigger_type,
                            "reason": "empty_response",
                            "trigger_reason": best_trigger.reason[:200],
                        },
                    )
                )

        except Exception as exc:
            logger.error("Failed to generate response: %s", exc)
            self.db.append_audit(
                AuditEntry(
                    participant_id=self._participant.participant_id,
                    meeting_id=meeting.meeting_id,
                    event_type=AuditEventType.ERROR,
                    payload={"error": str(exc), "trigger": best_trigger.trigger_type},
                )
            )

    async def start(self) -> None:
        """Start the Slack bot."""
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not app_token:
            raise ValueError(
                "SLACK_APP_TOKEN environment variable is required. "
                "Create a Socket Mode app token at api.slack.com."
            )

        handler = AsyncSocketModeHandler(self.app, app_token)
        logger.info("Starting %s Slack bot...", self.participant_name)
        await handler.start_async()


def _chunk_text(content: str, limit: int = 3800) -> list[str]:
    """Split a long document into Slack-sized chunks at natural boundaries.

    Prefers paragraph breaks, then line breaks, and only slices raw text as a
    last resort — the naive `content[i:i+limit]` split was cutting documents
    mid-word and mid-bold ("**condition" / "al contingency**"), mangling the
    exact passages that carry decisions.
    """
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    remaining = content
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n ")
    if remaining:
        chunks.append(remaining)
    return chunks


class MeetingSession:
    """Tracks an active meeting session."""

    def __init__(
        self,
        meeting: Meeting,
        engine: ParticipationEngine,
        channel_id: str,
    ) -> None:
        self.meeting = meeting
        self.engine = engine
        self.channel_id = channel_id


# Import here to avoid circular - used by _check_addressed
from src.models import TriggerResult  # noqa: E402
