"""Participation engine — the core decision loop.

Evaluates the meeting event stream against mode configuration.
Decides when the AI should contribute and generates responses
via the LLM adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

from src.engine.modes import ModeConfig, generate_introduction
from src.llm.adapter import LLMAdapter
from src.models import (
    MEMORY_TYPES,
    ExtractedMemory,
    MeetingEvent,
    TriggerResult,
)

# Cap on memories extracted from a single meeting (D10) — keep the durable set
# small so injected context stays focused and cheap.
MAX_MEMORIES_PER_MEETING = 10

# Minimum messages between two firings of the same *continuous-condition*
# trigger. `circular_discussion` and `synthesis_opportunity` describe a state
# of the meeting, not an event in it: once a discussion has several active
# speakers or a few repeated terms, that stays true for the rest of the
# meeting, so an ungated evaluation fires on every subsequent message and the
# AI's speaking rate ends up decided by its rate limiter rather than by
# anything that happened. Roles override this per trigger with
# `extra.min_messages_between`.
DEFAULT_TRIGGER_REFRACTORY = 12

# Punctuation stripped from the first word when checking whether a message
# opens by naming someone: "Sarah," / "Sarah:" / "@Sarah" / "Sarah -".
_VOCATIVE_PUNCTUATION = ",:;-—–@!.?\"'"

logger = logging.getLogger(__name__)


# Cheap claim-marker patterns for the assertive pre-filter (D11). These gate
# the expensive LLM detection call so most meeting chatter never reaches the
# model. Deliberately permissive — the LLM is the real judge of whether a
# statement contains a verifiable error; this only skips obvious non-claims.
_RE_NUMBER = re.compile(r"\d")
_RE_COPULA = re.compile(
    r"\b(is|are|was|were|isn't|aren't|wasn't|weren't|has|have|had|does|do|did|"
    r"can|cannot|can't|will|won't)\b",
    re.IGNORECASE,
)
_RE_ABSOLUTE = re.compile(
    r"\b(always|never|all|every|everyone|none|no one|nobody|only|must|"
    r"impossible|guaranteed|fundamentally|by default|entirely|completely)\b",
    re.IGNORECASE,
)
# CamelCase product names (MongoDB) or ALL-CAPS acronyms (HTTP, ACID, GIL, JSON).
_RE_TECH = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z]{2,}\b")


class ParticipationEngine:
    """Evaluates triggers and generates AI contributions.

    This is the product's core IP — the logic that determines when
    and how the AI participates in a meeting.
    """

    def __init__(
        self,
        mode: ModeConfig,
        llm: LLMAdapter,
        participant_name: str = "Synedrios",
        participant_id: str = "",
        meeting_id: str = "",
    ) -> None:
        self.mode = mode
        self.llm = llm
        self.participant_name = participant_name
        self.participant_id = participant_id
        self.meeting_id = meeting_id

        # Turn management state
        self._last_contribution_time: float = 0
        self._contributions_window: list[float] = []  # timestamps of recent contributions
        self._contribution_count: int = 0

        # Refractory state, measured in messages rather than seconds: how many
        # messages the engine has evaluated, and the count at which each
        # trigger type last produced a contribution. A directly-addressed
        # message bypasses trigger evaluation and so does not advance the
        # clock, which can only make a state trigger wait longer.
        self._messages_seen: int = 0
        self._last_contribution_at: dict[str, int] = {}

        # Cross-meeting memory injected into the system prompt (D10). Populated
        # by the connector at meeting start from the participant's stored memory.
        self.injected_memories: list[str] = []

    def get_introduction(self) -> str:
        """Generate the meeting introduction text."""
        return generate_introduction(self.mode, self.participant_name)

    def evaluate_triggers(
        self, event: MeetingEvent, transcript: list[MeetingEvent]
    ) -> list[TriggerResult]:
        """Evaluate all configured triggers against the current event.

        Returns a list of triggers that fired, sorted by priority.
        """
        if self.mode.initiative.level == 0:
            return []  # Silent mode — never trigger

        self._messages_seen += 1
        fired: list[TriggerResult] = []

        for trigger_cfg in self.mode.initiative.triggers:
            result = self._evaluate_single_trigger(trigger_cfg.type, trigger_cfg, event, transcript)
            if result and result.fired:
                fired.append(result)

        # Sort by priority (lower number = higher priority)
        fired.sort(key=lambda t: t.priority)
        return fired

    def _evaluate_single_trigger(
        self,
        trigger_type: str,
        config: Any,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
    ) -> Optional[TriggerResult]:
        """Evaluate a single trigger type."""

        if trigger_type == "addressed_directly":
            return self._check_addressed(event, config, transcript)
        elif trigger_type == "factual_error":
            # LLM-based; evaluated asynchronously in evaluate_llm_triggers().
            return None
        elif trigger_type == "contradiction":
            # LLM-based; evaluated asynchronously in evaluate_llm_triggers().
            return None
        elif trigger_type == "circular_discussion":
            return self._check_circular(transcript, config)
        elif trigger_type == "synthesis_opportunity":
            return self._check_synthesis_opportunity(transcript, config)
        elif trigger_type == "scheduled_turn":
            return None  # handled by external turn scheduler
        else:
            return None

    def _check_addressed(
        self,
        event: MeetingEvent,
        config: Any,
        transcript: Optional[list[MeetingEvent]] = None,
    ) -> Optional[TriggerResult]:
        """Check if the AI was directly addressed."""
        content_lower = event.content.lower()
        name_lower = self.participant_name.lower()

        # Check for name mention or @mention
        if name_lower in content_lower or f"@{name_lower}" in content_lower:
            return TriggerResult(
                trigger_type="addressed_directly",
                fired=True,
                confidence=1.0,
                priority=1,
                reason=f"Addressed by {event.speaker}",
                source_event_id=event.event_id,
            )

        # Check for question marks directed at the group
        # (in respond-only mode, only respond to direct address)
        if self.mode.initiative.level <= 1:
            return None

        # From reactive initiative upward, an unaddressed question to the room
        # may draw the AI in. This is reported as `open_question`, not
        # `addressed_directly`: nobody addressed the AI, and the audit log is
        # the legal record of why it spoke, so the two cases must not share a
        # label. Roles whose job is process rather than answers turn it off
        # with `extra.respond_to_open_questions: false`.
        extra = getattr(config, "extra", {}) or {}
        if not extra.get("respond_to_open_questions", True):
            return None

        # A question with somebody's name on it was put to them, not to the
        # room, and answering it over their shoulder is exactly the kind of
        # interruption the participation model exists to prevent.
        if self._is_directed_at_someone_else(event, transcript or []):
            return None

        if "?" in event.content and self.mode.initiative.level >= 4:
            return TriggerResult(
                trigger_type="open_question",
                fired=True,
                confidence=0.5,
                priority=getattr(config, "priority", 3) + 2,
                reason="Open question to the room",
                source_event_id=event.event_id,
            )

        return None

    def _is_directed_at_someone_else(
        self, event: MeetingEvent, transcript: list[MeetingEvent]
    ) -> bool:
        """Was this message aimed at a named participant other than the AI?

        Two signals, matching how people actually address each other in chat:

        1. An @-mention of a colleague. The connector resolves mentions to
           names and records them in `metadata["directed_at"]`, having already
           removed the AI's own mention — a message that mentions the AI
           reaches it as a direct address, not as an open question.
        2. The message opens with a participant's name: "Sarah, can you check
           the migration?". Names are taken from who has spoken in this meeting,
           so no roster is needed and the check costs nothing.

        The second rule deliberately errs toward silence. "Mike said the build
        was green — is that right?" opens with a participant's name and will be
        skipped even though it is a question to the room. Staying quiet when the
        AI might have spoken is the cheap mistake; answering a question meant
        for a colleague is the expensive one.
        """
        directed_at = event.metadata.get("directed_at") or []
        if isinstance(directed_at, list) and any(
            str(name).strip().lower() != self.participant_name.lower()
            for name in directed_at
            if str(name).strip()
        ):
            return True

        content = (event.content or "").strip()
        if not content:
            return False

        speakers = {
            n for n in self._known_speakers(transcript) if n != self.participant_name.lower()
        }
        first_words = {n.split()[0] for n in speakers}

        # Rule A — the message opens with a participant's name, with or
        # without punctuation ("Mike, can you…" / "Mike can you…").
        opening = content.split()[0].strip(_VOCATIVE_PUNCTUATION).lower()
        if opening and (opening in speakers or opening in first_words):
            return True

        # Rule B — a later sentence opens with a name followed by a comma or
        # colon: "Let's keep it civil. Mike, what's the actual number?" is
        # Mike's question (live-rehearsal finding — the AI answered it over
        # Mike's shoulder). The punctuation requirement keeps "Mike is right."
        # from silencing the AI mid-message.
        for sentence in re.split(r"[.!?]\s+", content)[1:]:
            tokens = sentence.strip().split()
            if not tokens:
                continue
            token = tokens[0]
            if token.endswith((",", ":")):
                name = token.rstrip(",:").lower()
                if name in speakers or name in first_words:
                    return True
        return False

    @staticmethod
    def _known_speakers(transcript: list[MeetingEvent]) -> set[str]:
        """Lower-cased names of everyone who has spoken in this meeting."""
        return {(e.speaker or "").strip().lower() for e in transcript if (e.speaker or "").strip()}

    def _refractory_elapsed(self, trigger_type: str, config: Any) -> bool:
        """Has enough of the meeting passed to raise this state trigger again?

        Returns True the first time, and thereafter only once
        `min_messages_between` messages have been evaluated since this trigger
        last led to a contribution.
        """
        last = self._last_contribution_at.get(trigger_type)
        if last is None:
            return True
        extra = getattr(config, "extra", {}) or {}
        gap = int(extra.get("min_messages_between", DEFAULT_TRIGGER_REFRACTORY))
        return (self._messages_seen - last) >= gap

    def _check_circular(
        self, transcript: list[MeetingEvent], config: Any
    ) -> Optional[TriggerResult]:
        """Detect circular discussion (same points repeated)."""
        threshold = config.extra.get("repetition_threshold", 3)
        if len(transcript) < 10:
            return None
        if not self._refractory_elapsed("circular_discussion", config):
            return None

        # Simple heuristic: check if the last N messages contain
        # repeated key phrases. Full implementation would use
        # the LLM for semantic similarity.
        recent = [e.content.lower() for e in transcript[-20:] if e.speaker]
        if len(recent) < 6:
            return None

        # Count rough repetitions using word overlap
        from collections import Counter

        word_counts: Counter[str] = Counter()
        for msg in recent:
            words = set(msg.split())
            for w in words:
                if len(w) > 4:  # skip short words
                    word_counts[w] += 1

        # If several content words appear in many messages, likely circular
        repeated = sum(1 for w, c in word_counts.items() if c >= threshold)
        if repeated >= 3:
            return TriggerResult(
                trigger_type="circular_discussion",
                fired=True,
                confidence=0.7,
                priority=5,
                reason=f"Detected {repeated} repeated terms across recent messages",
            )
        return None

    def _check_synthesis_opportunity(
        self, transcript: list[MeetingEvent], config: Any
    ) -> Optional[TriggerResult]:
        """Detect when synthesis might be helpful."""
        min_threads = config.extra.get("min_unresolved_threads", 3)
        if len(transcript) < 15:
            return None
        if not self._refractory_elapsed("synthesis_opportunity", config):
            return None

        # Heuristic: multiple different speakers making substantive points
        recent = transcript[-20:]
        speakers = set(e.speaker for e in recent if e.speaker)
        if len(speakers) >= min_threads:
            return TriggerResult(
                trigger_type="synthesis_opportunity",
                fired=True,
                confidence=0.6,
                priority=4,
                reason=f"{len(speakers)} speakers with unresolved threads",
            )
        return None

    # ----- LLM-based detection (factual_error, contradiction) -----

    def _looks_assertive(self, text: str) -> bool:
        """Cheap pre-filter: does the message look like a verifiable claim?

        Gates the expensive LLM detection call (D11). A message passes if it
        carries a claim marker — a number, an absolute quantifier, a copula
        ("is/are/was…"), or a product/acronym token. Greetings, acknowledgements,
        very short lines, and bare questions are filtered out.
        """
        t = (text or "").strip()
        tokens = t.split()
        if len(tokens) < 4:
            return False
        # A bare question with no declarative copula is not an assertion.
        if t.endswith("?") and _RE_COPULA.search(t) is None:
            return False
        if (
            _RE_NUMBER.search(t)
            or _RE_ABSOLUTE.search(t)
            or _RE_COPULA.search(t)
            or _RE_TECH.search(t)
        ):
            return True
        # A capitalised proper noun mid-sentence usually names an entity or
        # technology (Python, Redis, Kubernetes) — treat it as a possible claim.
        for tok in tokens[1:]:
            w = tok.strip(".,;:!?\"'()[]{}")
            if len(w) >= 3 and w[0].isupper() and w[1].islower():
                return True
        return False

    def _context_excluding(
        self,
        transcript: list[MeetingEvent],
        event: MeetingEvent,
        n: Optional[int] = None,
    ) -> tuple[str, list[MeetingEvent]]:
        """Return (formatted context, prior events) with `event` removed.

        The transcript handed to the engine includes the just-saved event, so
        detectors exclude it by id to reason only about what came before.
        """
        prior = [
            e for e in transcript if e.event_id != event.event_id and (e.content or "").strip()
        ]
        window = prior[-n:] if n is not None else prior
        text = "\n".join(f"[{e.speaker or 'Unknown'}]: {e.content}" for e in window)
        return text, prior

    @staticmethod
    def _salvage_memory_array(raw: str) -> Optional[list[dict[str, Any]]]:
        """Recover complete items from a truncated {"memories": [...]} reply.

        A response cut off at max_tokens loses only its tail; the complete
        leading objects are still worth keeping. Returns None when nothing
        recoverable is found (caller treats that as a hard failure).
        """
        if not raw:
            return None
        start = raw.find("[", max(raw.find('"memories"'), 0))
        if start == -1:
            return None
        items: list[dict[str, Any]] = []
        depth = 0
        obj_start = -1
        in_string = False
        escape = False
        for i in range(start + 1, len(raw)):
            ch = raw[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start != -1:
                    try:
                        obj = json.loads(raw[obj_start : i + 1])
                        if isinstance(obj, dict):
                            items.append(obj)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    obj_start = -1
            elif ch == "]" and depth == 0:
                break
        return items or None

    @staticmethod
    def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
        """Extract the first JSON object from a model response, tolerantly.

        Handles code fences and surrounding prose by slicing from the first
        '{' to the last '}'. Returns None if nothing parses.
        """
        if not raw:
            return None
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None

    @staticmethod
    def _as_confidence(value: Any) -> float:
        """Coerce a model-supplied confidence into a clamped 0..1 float."""
        try:
            c = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, c))

    async def _detect_factual_error(
        self,
        config: Any,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
    ) -> Optional[TriggerResult]:
        """Ask the LLM whether the latest statement holds a verifiable error."""
        if not self._looks_assertive(event.content):
            return None
        context, _ = self._context_excluding(transcript, event, n=8)
        system = (
            "You are a meticulous fact-checker embedded in a live meeting. "
            "Judge ONLY whether the latest statement contains a verifiable, "
            "objective factual error (technology, mathematics, science, history). "
            "Opinions, predictions, preferences, strategy, and value judgements "
            "are NOT factual errors. Reply with ONLY a JSON object of the form "
            '{"error": true|false, "is_opinion": true|false, '
            '"confidence": <0..1>, "correction": "<short correction or empty>"}. '
            "confidence is how certain you are a real, verifiable error is present."
        )
        user = (
            f"Recent discussion:\n{context or '(none)'}\n\n"
            f"Latest statement by {event.speaker or 'a participant'}: "
            f'"{event.content}"\n\nReturn the JSON only.'
        )
        raw = await self.llm.complete(
            messages=[{"role": "user", "content": user}],
            system=system,
            # Generous ceiling, tiny actual spend: adaptive thinking bills
            # against the same budget, and a starved call returns truncated
            # JSON that parses to nothing (T6 live-rehearsal finding).
            max_tokens=1024,
            temperature=0.0,
        )
        data = self._parse_json_object(raw)
        if data is None:
            # Unparseable response is a detector FAILURE, not a negative —
            # it must reach the audit log, or detection health is invisible
            # exactly when budgets or prompts break it.
            return self._detector_failure("factual_error", config, event, raw)
        if not data.get("error") or data.get("is_opinion"):
            return None
        confidence = self._as_confidence(data.get("confidence"))
        threshold = getattr(config, "confidence_threshold", 0.9)
        correction = str(data.get("correction") or "").strip()
        return TriggerResult(
            trigger_type="factual_error",
            fired=confidence >= threshold,
            confidence=confidence,
            priority=getattr(config, "priority", 2),
            reason=(correction or "Possible factual error")[:300],
            source_event_id=event.event_id,
            context={"threshold": threshold, "correction": correction},
        )

    async def _detect_contradiction(
        self,
        config: Any,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
    ) -> Optional[TriggerResult]:
        """Ask the LLM whether the latest statement reverses an earlier one."""
        if not self._looks_assertive(event.content):
            return None
        context, prior = self._context_excluding(transcript, event)
        if len(prior) < 2:
            return None  # nothing earlier to contradict
        system = (
            "You detect self-contradictions and reversals in a live meeting. "
            "Judge whether the LATEST statement directly contradicts something "
            "asserted EARLIER in the same meeting. A differing opinion is NOT a "
            "contradiction; a factual reversal (asserting X earlier and not-X "
            "now) is. Reply with ONLY a JSON object of the form "
            '{"contradiction": true|false, "confidence": <0..1>, '
            '"earlier_statement": "<contradicted earlier statement or empty>", '
            '"explanation": "<short explanation or empty>"}.'
        )
        user = (
            f"Meeting so far:\n{context}\n\n"
            f"Latest statement by {event.speaker or 'a participant'}: "
            f'"{event.content}"\n\nReturn the JSON only.'
        )
        raw = await self.llm.complete(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=1024,  # see _detect_factual_error — headroom for thinking
            temperature=0.0,
        )
        data = self._parse_json_object(raw)
        if data is None:
            return self._detector_failure("contradiction", config, event, raw)
        if not data.get("contradiction"):
            return None
        confidence = self._as_confidence(data.get("confidence"))
        threshold = getattr(config, "confidence_threshold", 0.8)
        explanation = str(data.get("explanation") or "").strip()
        return TriggerResult(
            trigger_type="contradiction",
            fired=confidence >= threshold,
            confidence=confidence,
            priority=getattr(config, "priority", 3),
            reason=(explanation or "Possible contradiction with an earlier statement")[:300],
            source_event_id=event.event_id,
            context={
                "threshold": threshold,
                "earlier_statement": str(data.get("earlier_statement") or "").strip(),
                "explanation": explanation,
            },
        )

    @staticmethod
    def _detector_failure(
        trigger_type: str, config: Any, event: MeetingEvent, raw: str
    ) -> TriggerResult:
        """A detector that could not produce a verdict, as an auditable result.

        fired=False keeps it out of the contribution path; the connector
        audits it with reason "detector_failed" so calibration can tell a
        broken detector from a quiet meeting.
        """
        return TriggerResult(
            trigger_type=trigger_type,
            fired=False,
            confidence=0.0,
            priority=getattr(config, "priority", 5),
            reason="detector response unparseable",
            source_event_id=event.event_id,
            context={"detector_failed": True, "raw_head": (raw or "")[:160]},
        )

    async def evaluate_llm_triggers(
        self,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
    ) -> list[TriggerResult]:
        """LLM-based detection for factual_error and contradiction (async).

        Kept separate from the synchronous, deterministic evaluate_triggers()
        because each detector may issue an LLM call. Calls are gated by a cheap
        heuristic pre-filter (_looks_assertive), and the configured detectors run
        concurrently so added latency is roughly one model round-trip rather than
        the sum. Returns every detection; callers audit any below-threshold
        detection as suppressed so the confidence threshold can be calibrated
        against real meetings (D11).
        """
        if self.mode.initiative.level == 0:
            return []  # Silent mode — never analyse
        coros = []
        coro_cfgs = []
        for cfg in self.mode.initiative.triggers:
            if cfg.type == "factual_error":
                coros.append(self._detect_factual_error(cfg, event, transcript))
                coro_cfgs.append(cfg)
            elif cfg.type == "contradiction":
                coros.append(self._detect_contradiction(cfg, event, transcript))
                coro_cfgs.append(cfg)
        if not coros:
            return []
        settled = await asyncio.gather(*coros, return_exceptions=True)
        results: list[TriggerResult] = []
        for cfg, outcome in zip(coro_cfgs, settled):
            if isinstance(outcome, BaseException):
                # An exception (network, API) is a detector failure too — it
                # must be auditable, not just a log line that nobody reads.
                logger.warning("LLM trigger detection failed: %s", outcome)
                failure = self._detector_failure(cfg.type, cfg, event, "")
                failure.context["raw_head"] = f"exception: {outcome}"[:160]
                results.append(failure)
            elif outcome is not None:
                results.append(outcome)
        # Fired detections first, then by priority (lower = higher priority).
        results.sort(key=lambda t: (not t.fired, t.priority))
        return results

    def can_contribute(self) -> bool:
        """Check turn management constraints."""
        now = time.time()

        # Cooldown check
        cooldown = self.mode.initiative.cooldown_seconds
        if now - self._last_contribution_time < cooldown:
            return False

        # Rate limit check (contributions per 10 minutes)
        window_start = now - 600
        self._contributions_window = [t for t in self._contributions_window if t > window_start]
        if len(self._contributions_window) >= self.mode.initiative.max_contributions_per_10min:
            return False

        return True

    def record_contribution(self, trigger_type: str = "") -> None:
        """Record that a contribution was made (for turn management).

        `trigger_type` starts that trigger's refractory period, so a trigger
        describing a continuous state does not raise the same observation on
        every following message.
        """
        now = time.time()
        self._last_contribution_time = now
        self._contributions_window.append(now)
        self._contribution_count += 1
        if trigger_type:
            self._last_contribution_at[trigger_type] = self._messages_seen

    async def generate_response(
        self,
        trigger: TriggerResult,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
        memories: list[str] | None = None,
    ) -> str:
        """Generate the AI's contribution via the LLM.

        Falls back to the engine's injected cross-meeting memories when the
        caller does not pass an explicit list, so prior-meeting knowledge
        reaches every live contribution.
        """
        if memories is None:
            memories = self.injected_memories

        system_prompt = self._build_system_prompt(memories)
        messages = self._build_messages(trigger, event, transcript)

        # The word cap lives in the system prompt; the token ceiling only
        # protects against runaway output. words*3 left adaptive thinking
        # almost no headroom on current models — a floor of 1024 costs
        # nothing (ceilings are not spend) and prevents silent truncation.
        words = self.mode.initiative.contribution_length_words
        response = await self.llm.complete(
            messages=messages,
            system=system_prompt,
            max_tokens=max(1024, words * 4),
            temperature=0.7,
        )
        return response.strip()

    async def generate_post_meeting(
        self, transcript: list[MeetingEvent], meeting_title: str, style: str = ""
    ) -> dict[str, str]:
        """Generate post-meeting outputs (notes, action items, summary).

        style="slack" appends mrkdwn formatting rules — Slack does not render
        GitHub Markdown, so # headings, **bold**, and pipe tables arrive as
        literal punctuation for the customer (T6 live-rehearsal finding).
        """
        outputs = {}
        transcript_text = self._format_transcript(transcript)

        for output_type in self.mode.post_meeting.outputs:
            prompt = self._build_post_meeting_prompt(output_type, transcript_text, meeting_title)
            prompt += (
                "\n\nGround rules for this document:\n"
                "- Report only what the transcript supports. Never invent items, "
                "owners, deadlines, attendees, or timestamps; when citing a time, "
                "use the transcript's own [HH:MM:SS] stamps.\n"
                f"- Lines from {self.participant_name} are the AI participant's own "
                "contributions during the meeting. Record and attribute them like "
                "any other participant's — the document must not omit them or "
                "credit their content to someone else.\n"
                "- Do not assign the AI participant any task the transcript did "
                "not explicitly give it."
            )
            if style == "slack":
                prompt += (
                    "\n\nFormat for Slack (mrkdwn), NOT GitHub Markdown:\n"
                    "- Headings: a short *bold* line (single asterisks), never # or ##.\n"
                    "- Bold is *single asterisks*; italics _underscores_. Never **double**.\n"
                    "- Bullets: hyphen lists. No tables — use 'Owner — task (deadline)' "
                    "lines instead. No horizontal rules."
                )
            result = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system="You are a precise meeting assistant producing post-meeting documents.",
                max_tokens=8192,
                temperature=0.3,
            )
            outputs[output_type] = result.strip()

        return outputs

    async def extract_memories(
        self,
        transcript: list[MeetingEvent],
        existing_contents: list[str],
    ) -> list[ExtractedMemory]:
        """Extract durable, typed memories from a finished meeting (D10).

        One LLM pass over the full transcript. The model is given the memories
        already known about this participant so it can skip them and flag any
        that a new statement supersedes. Output is capped, typed, and filtered:
        only person/decision/fact/preference/relationship memories survive, and
        an exact-content match against existing memory is dropped as a
        deterministic dedup safety net on top of the model's semantic check.
        """
        if not transcript:
            return []

        transcript_text = self._format_transcript(transcript)
        existing_block = (
            "\n".join(f"- {c}" for c in existing_contents) if existing_contents else "(none yet)"
        )
        system = (
            "You extract durable institutional knowledge from a finished meeting "
            "so an AI participant can recall it in future meetings. Capture ONLY "
            "facts worth remembering long-term, each typed as one of: person "
            "(a durable fact about a participant), decision (what was decided), "
            "fact (a durable factual claim), preference (a stated preference), "
            "relationship (how people/teams relate). Do NOT capture chit-chat, "
            "transient status, or anything already in the known memories unless a "
            'statement CORRECTS or SUPERSEDES it (mark those status="update" and '
            "set supersedes to the exact known-memory text). A deadline phrased "
            "relative to the meeting ('by Thursday', 'on Friday') is transient "
            "status, not durable knowledge — it belongs in action items, never "
            "in memory. Who OWNS an area is durable; when their next task is due "
            "is not. Be concise and "
            f"conservative: at most {MAX_MEMORIES_PER_MEETING} high-confidence "
            "memories. Reply with ONLY a JSON object of the form "
            '{"memories": [{"type": "...", "content": "...", '
            '"confidence": <0..1>, "status": "new"|"update", "supersedes": "..."}]}.'
        )
        user = (
            f"Known memories about this participant:\n{existing_block}\n\n"
            f"Meeting transcript:\n{transcript_text}\n\n"
            "Return the JSON only."
        )
        raw = await self.llm.complete(
            messages=[{"role": "user", "content": user}],
            system=system,
            # 1200 was too tight: 10 memories plus adaptive thinking could
            # exceed it, truncating the JSON mid-array — and the parse
            # failure then lost EVERY memory from the meeting (the
            # silent-observer zero in the T6 live rehearsal).
            max_tokens=4096,
            temperature=0.2,
        )
        data = self._parse_json_object(raw)
        if not data:
            salvaged = self._salvage_memory_array(raw)
            if salvaged is None:
                # Unparseable and unsalvageable — this is a failure the
                # caller must surface, not an empty meeting.
                raise ValueError(f"memory extraction response unparseable: {(raw or '')[:160]!r}")
            data = {"memories": salvaged}
        items = data.get("memories")
        if not isinstance(items, list):
            return []

        existing_set = {c.strip().lower() for c in existing_contents}
        seen: set[str] = set()
        results: list[ExtractedMemory] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mtype = str(item.get("type", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            if mtype not in MEMORY_TYPES or not content:
                continue
            key = content.lower()
            if key in existing_set or key in seen:
                continue  # already known, or a duplicate within this batch
            seen.add(key)
            status = str(item.get("status", "new")).strip().lower()
            results.append(
                ExtractedMemory(
                    memory_type=mtype,
                    content=content,
                    confidence=self._as_confidence(item.get("confidence", 0.8)),
                    status=status if status in ("new", "update") else "new",
                    supersedes=str(item.get("supersedes", "")).strip(),
                )
            )
            if len(results) >= MAX_MEMORIES_PER_MEETING:
                break
        return results

    def _build_system_prompt(self, memories: list[str] | None = None) -> str:
        """Build the LLM system prompt from mode configuration."""
        role_name = self.mode.name.replace("_", " ").title()
        parts = [
            f"You are {self.participant_name}, an AI meeting participant.",
            f"Your role: {role_name}.",
        ]

        if self.mode.description:
            parts.append(f"Role description: {self.mode.description}")

        if self.mode.personality_note:
            parts.append(f"Personality: {self.mode.personality_note}")

        # Contribution constraints
        enabled = self.mode.contributions_enabled
        if enabled:
            readable = [c.replace("_", " ") for c in enabled]
            parts.append(f"You may contribute: {', '.join(readable)}.")

        disabled = self.mode.contributions_disabled
        if disabled:
            readable = [c.replace("_", " ") for c in disabled]
            parts.append(f"You must NOT contribute: {', '.join(readable)}.")

        # Tone
        tone = self.mode.tone
        tone_labels = {
            "formality": {1: "casual", 2: "professional", 3: "formal", 4: "ceremonial"},
            "directness": {1: "blunt", 2: "direct", 3: "diplomatic", 4: "indirect"},
            "warmth": {1: "reserved", 2: "neutral", 3: "warm", 4: "relational"},
            "hedging": {1: "assertive", 2: "measured", 3: "cautious"},
        }
        tone_desc = []
        for dim, labels in tone_labels.items():
            val = getattr(tone, dim, 2)
            if val in labels:
                tone_desc.append(f"{dim}: {labels[val]}")
        if tone_desc:
            parts.append(f"Tone: {', '.join(tone_desc)}.")

        # Authority
        auth_labels = {1: "observer", 2: "advisor", 3: "peer", 4: "expert", 5: "chair"}
        auth_label = auth_labels.get(self.mode.authority, "advisor")
        parts.append(f"Authority level: {auth_label}.")

        # Length constraint
        max_words = self.mode.initiative.contribution_length_words
        parts.append(
            f"Keep contributions concise — under {max_words} words. Be direct and substantive."
        )

        # Memories — temporally grounded: in the live rehearsal the AI voiced
        # an injected memory as established fact a minute BEFORE the matching
        # statement was made in the meeting, which reads as contamination.
        if memories:
            parts.append(
                "\nKnowledge you carry from PREVIOUS meetings (none of it has "
                "been said in THIS meeting):"
            )
            for mem in memories[:10]:
                parts.append(f"- {mem}")
            parts.append(
                "If you draw on one of these, attribute it as prior knowledge "
                "('from a previous meeting…', 'as previously agreed…') — never "
                "present it as something already said today."
            )

        return "\n".join(parts)

    def _build_messages(
        self,
        trigger: TriggerResult,
        event: MeetingEvent,
        transcript: list[MeetingEvent],
    ) -> list[dict[str, str]]:
        """Build the message history for the LLM call."""
        # Include recent transcript as context
        context_parts = []
        for e in transcript[-30:]:
            speaker = e.speaker or "Unknown"
            context_parts.append(f"[{speaker}]: {e.content}")

        transcript_text = "\n".join(context_parts)

        user_content = (
            f"Meeting transcript (recent):\n\n{transcript_text}\n\n"
            f"---\n"
            f"Trigger: {trigger.trigger_type} — {trigger.reason}\n"
            f"Latest message from {event.speaker}: {event.content}\n\n"
            f"Generate your contribution to the meeting. "
            f"Speak naturally as a meeting participant, not as an AI assistant. "
            f"Do not use markdown formatting — this is spoken/chat text."
        )

        return [{"role": "user", "content": user_content}]

    def _build_post_meeting_prompt(
        self, output_type: str, transcript_text: str, meeting_title: str
    ) -> str:
        """Build the prompt for a post-meeting output."""
        prompts = {
            "structured_notes": (
                f"Produce structured meeting notes for: {meeting_title}\n\n"
                f"Organise by topic or agenda item. Include:\n"
                f"- Key discussion points (who said what)\n"
                f"- Decisions made\n"
                f"- Open questions\n"
                f"- Parking lot items\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "executive_summary": (
                f"Write a 200-500 word executive summary of: {meeting_title}\n"
                f"Written for someone who was not present.\n"
                f"Cover: what happened, what was decided, what's next.\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "action_items_by_owner": (
                f"Extract ALL action items from this meeting: {meeting_title}\n\n"
                f"For each action item provide:\n"
                f"- Owner (who is responsible)\n"
                f"- Task (what they need to do)\n"
                f"- Deadline (if mentioned)\n"
                f"- Context (what was said that generated this task)\n\n"
                f"Group by owner. Include tasks for people mentioned but not present.\n"
                f"Only include a task for the AI participant if someone explicitly "
                f"assigned it one in the transcript — never invent AI follow-ups.\n\n"
                f"Format as structured text with clear headers per owner.\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "decision_log": (
                f"Extract all decisions made during: {meeting_title}\n\n"
                f"For each decision:\n"
                f"- What was decided\n"
                f"- Who made/approved the decision\n"
                f"- Any conditions or caveats\n"
                f"- Any dissent noted\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "follow_up_agenda": (
                f"Suggest an agenda for the follow-up meeting based on: {meeting_title}\n\n"
                f"Include:\n"
                f"- Unresolved items from this meeting\n"
                f"- Parked topics\n"
                f"- Action item reviews\n"
                f"- New items implied by discussion\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "risk_register": (
                f"Extract risks identified or implied during: {meeting_title}\n\n"
                f"For each risk:\n"
                f"- Description\n"
                f"- Severity (critical/high/medium/low)\n"
                f"- Who raised it (or if it was implied)\n"
                f"- Any mitigation discussed\n\n"
                f"Transcript:\n{transcript_text}"
            ),
            "coaching_assessment": (
                f"Write a coaching assessment of the meeting: {meeting_title}\n\n"
                f"You observed silently as a meeting coach. For each participant "
                f"who spoke:\n"
                f"- Strengths: what they did well, with a [HH:MM:SS] example\n"
                f"- Development areas: specific, constructive, with a timestamped "
                f"example — never a personal judgement\n"
                f"Then assess the meeting itself: pacing, participation balance, "
                f"decision quality, and how conflict was handled.\n"
                f"Be specific and kind; every observation needs a moment from the "
                f"transcript behind it.\n\n"
                f"Transcript:\n{{transcript}}"
            ).replace("{{transcript}}", transcript_text),
            "improvement_tasks": (
                f"Based on the coaching observations from: {meeting_title}\n\n"
                f"Suggest at most five concrete practices for the team's next "
                f"meeting (e.g. timeboxing a recurring discussion, a decision "
                f"protocol, balancing airtime). For each: what to do, who it "
                f"helps, and which moment in this meeting shows why.\n\n"
                f"Transcript:\n{{transcript}}"
            ).replace("{{transcript}}", transcript_text),
            "ai_task_list": (
                f"Based on the discussion in: {meeting_title}\n\n"
                f"What research, analysis, or preparation should the AI participant "
                f"do before the next meeting? List specific, actionable tasks.\n\n"
                f"Transcript:\n{transcript_text}"
            ),
        }

        return prompts.get(
            output_type,
            (
                f"Generate {output_type.replace('_', ' ')} for: {meeting_title}\n\n"
                f"Transcript:\n{transcript_text}"
            ),
        )

    def _format_transcript(self, transcript: list[MeetingEvent]) -> str:
        """Format transcript events into readable text."""
        lines = []
        for e in transcript:
            ts = e.timestamp.strftime("%H:%M:%S") if e.timestamp else ""
            speaker = e.speaker or "System"
            lines.append(f"[{ts}] {speaker}: {e.content}")
        return "\n".join(lines)
