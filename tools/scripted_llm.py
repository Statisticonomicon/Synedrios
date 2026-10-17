"""Deterministic offline LLM adapter for rehearsals.

Lets `tools.rehearse` drive the entire participation stack — triggers, turn
management, contributions, post-meeting outputs, memory extraction — with no
network call, no API key, and no cost. Responses are shaped to match what each
call site expects, so the real parsing code is exercised.

**The detections are synthetic.** Offline mode answers the factual-error and
contradiction detectors from a deterministic hash of the message, not from any
understanding of it. That is enough to exercise the fired / suppressed / cooled
-down code paths reproducibly; it says nothing about detection quality. Judge
detection quality only from a `--live` rehearsal or a real meeting.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.llm.adapter import LLMAdapter


def _stable_unit(text: str, salt: str) -> float:
    """A stable pseudo-random float in [0, 1) derived from the text."""
    digest = hashlib.sha256(f"{salt}:{text}".encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


class ScriptedLLMAdapter(LLMAdapter):
    """An LLM stand-in that returns plausible, deterministic responses.

    Args:
        detect_rate: fraction of assertive messages the synthetic detectors
            report an error/contradiction for. 0.0 disables detection entirely.
        detect_confidence: confidence the synthetic detectors report. Set it
            below a role's threshold to exercise the suppressed path.
    """

    def __init__(self, detect_rate: float = 0.15, detect_confidence: float = 0.95) -> None:
        self.detect_rate = detect_rate
        self.detect_confidence = detect_confidence
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "scripted/offline"

    @property
    def context_window(self) -> int:
        return 200_000

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        user = messages[-1]["content"] if messages else ""

        if "fact-checker" in system:
            return self._factual_error(user)
        if "self-contradictions" in system:
            return self._contradiction(user)
        if "durable institutional knowledge" in system:
            return self._memories(user)
        if "post-meeting documents" in system:
            return self._post_meeting(user)
        return self._contribution(user)

    # ----- response shapes -------------------------------------------------

    def _factual_error(self, user: str) -> str:
        hit = _stable_unit(user, "factual") < self.detect_rate
        return json.dumps(
            {
                "error": hit,
                "is_opinion": False,
                "confidence": self.detect_confidence if hit else 0.05,
                "correction": "[offline rehearsal] synthetic correction" if hit else "",
            }
        )

    def _contradiction(self, user: str) -> str:
        hit = _stable_unit(user, "contradiction") < self.detect_rate
        return json.dumps(
            {
                "contradiction": hit,
                "confidence": self.detect_confidence if hit else 0.05,
                "earlier_statement": "[offline rehearsal] earlier statement" if hit else "",
                "explanation": "[offline rehearsal] synthetic contradiction" if hit else "",
            }
        )

    def _memories(self, user: str) -> str:
        return json.dumps(
            {
                "memories": [
                    {
                        "type": "decision",
                        "content": "[offline rehearsal] a decision was recorded",
                        "confidence": 0.8,
                        "status": "new",
                        "supersedes": "",
                    },
                    {
                        "type": "person",
                        "content": "[offline rehearsal] a durable fact about a participant",
                        "confidence": 0.7,
                        "status": "new",
                        "supersedes": "",
                    },
                ]
            }
        )

    def _post_meeting(self, user: str) -> str:
        head = user.strip().splitlines()[0][:80] if user.strip() else "output"
        return (
            f"[offline rehearsal] Generated document for: {head}\n\n"
            "- Point one\n- Point two\n- Point three"
        )

    def _contribution(self, user: str) -> str:
        return "[offline rehearsal] This is where the AI's contribution would go."
