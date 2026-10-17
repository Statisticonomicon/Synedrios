"""Audit-log analysis and threshold calibration (task T6).

Reads the append-only audit log — never writes to it — and reports how the
participant actually behaved: how often it spoke, what made it speak, what it
nearly said, and what silenced it. Turns the T6 acceptance criterion "trigger
thresholds and cooldowns tuned based on observed behaviour" into numbers
instead of impressions.

Usage:
    python -m tools.calibrate                         # every meeting in the DB
    python -m tools.calibrate --db data/synedrios.db  # a specific database
    python -m tools.calibrate --meeting <meeting_id>  # one meeting
    python -m tools.calibrate --role technical_advisor
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.models import AuditEventType, Meeting
from src.persistence.database import Database

# A role that speaks on more than this share of messages is dominating the
# meeting; below the floor it is arguably not earning its seat. Both are
# starting points to be revised from real meetings, not fixed truths.
SPEAK_RATE_CEILING = 0.20
SPEAK_RATE_FLOOR = 0.02


@dataclass
class MeetingStats:
    """Observed participation behaviour for a single meeting."""

    meeting_id: str
    title: str
    role: str
    status: str
    messages: int = 0
    contributions: int = 0
    triggers_fired: Counter[str] = field(default_factory=Counter)
    suppressed: Counter[str] = field(default_factory=Counter)
    throttled_by_trigger: Counter[str] = field(default_factory=Counter)
    detector_failures: list[str] = field(default_factory=list)
    below_threshold: list[tuple[str, float, str]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    memories: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def speak_rate(self) -> float:
        return self.contributions / self.messages if self.messages else 0.0


def collect(db: Database, meeting: Meeting) -> MeetingStats:
    """Derive one meeting's participation statistics from its audit entries."""
    mode = meeting.mode_config or {}
    stats = MeetingStats(
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        role=str(mode.get("name", "unknown")),
        status=meeting.status.value,
    )

    for entry in db.get_audit_log(meeting_id=meeting.meeting_id):
        payload = entry.payload
        if entry.event_type == AuditEventType.TRANSCRIPT_SEGMENT:
            stats.messages += 1
        elif entry.event_type == AuditEventType.CONTRIBUTION_MADE:
            stats.contributions += 1
            stats.triggers_fired[str(payload.get("trigger", "unknown"))] += 1
        elif entry.event_type == AuditEventType.TRIGGER_SUPPRESSED:
            reason = str(payload.get("reason", "unknown"))
            stats.suppressed[reason] += 1
            if reason == "below_threshold":
                stats.below_threshold.append(
                    (
                        str(payload.get("trigger", "unknown")),
                        float(payload.get("confidence", 0.0)),
                        str(payload.get("detail", ""))[:200],
                    )
                )
            elif reason == "cooldown_or_rate_limit":
                # Which triggers the throttle silenced — without this split
                # the first live rehearsal could not tell whether detection
                # worked on 4 of the 6 planted errors.
                for t in payload.get("all_triggers") or [
                    {"type": payload.get("trigger", "unknown")}
                ]:
                    stats.throttled_by_trigger[str(t.get("type", "unknown"))] += 1
            elif reason == "detector_failed":
                stats.detector_failures.append(
                    f"{payload.get('trigger', '?')}: {str(payload.get('detail', ''))[:120]}"
                )
        elif entry.event_type == AuditEventType.MEMORY_EXTRACTION_FAILED:
            stats.errors.append(f"memory extraction failed: {payload.get('error', '')}"[:160])
        elif entry.event_type == AuditEventType.POST_MEETING_OUTPUT:
            stats.outputs.append(str(payload.get("output_type", "unknown")))
        elif entry.event_type == AuditEventType.MEMORY_CREATED:
            stats.memories += 1
        elif entry.event_type == AuditEventType.ERROR:
            stats.errors.append(str(payload.get("error", ""))[:120])

    return stats


def thresholds_for(meeting: Meeting) -> dict[str, float]:
    """The confidence thresholds this meeting's role was configured with."""
    initiative = (meeting.mode_config or {}).get("initiative", {})
    out: dict[str, float] = {}
    for trigger in initiative.get("triggers", []):
        if isinstance(trigger, dict) and "confidence_threshold" in trigger:
            out[str(trigger.get("type"))] = float(trigger["confidence_threshold"])
    return out


def recommend(stats: MeetingStats, thresholds: dict[str, float]) -> list[str]:
    """Suggest threshold and cooldown changes from what was observed.

    Suggestions only — a role that spoke twice in a 90-message meeting may be
    correctly calibrated for a Secretary and badly calibrated for a Facilitator.
    The role's intent decides; these lines just say where to look.
    """
    notes: list[str] = []

    if stats.messages == 0:
        return ["No transcript recorded — nothing to calibrate."]

    if stats.speak_rate > SPEAK_RATE_CEILING:
        notes.append(
            f"Spoke on {stats.speak_rate:.0%} of messages (>{SPEAK_RATE_CEILING:.0%}). "
            f"Consider raising cooldown_seconds or lowering "
            f"max_contributions_per_10min for `{stats.role}`."
        )
    elif stats.contributions == 0:
        notes.append(
            "Never spoke. Correct for a silent role; for a reactive one, check "
            "whether detections were suppressed below threshold (see below)."
        )
    elif stats.speak_rate < SPEAK_RATE_FLOOR:
        notes.append(
            f"Spoke on only {stats.speak_rate:.0%} of messages. If the role is "
            f"meant to be reactive, consider lowering its confidence_threshold."
        )

    rate_limited = stats.suppressed.get("cooldown_or_rate_limit", 0)
    if rate_limited:
        notes.append(
            f"{rate_limited} contribution(s) blocked by cooldown/rate limit. "
            f"If those were the useful ones, the limits are too tight; if the "
            f"AI was already saying enough, they did their job."
        )

    by_trigger: dict[str, list[float]] = {}
    for trigger, confidence, _detail in stats.below_threshold:
        by_trigger.setdefault(trigger, []).append(confidence)

    for trigger, confidences in sorted(by_trigger.items()):
        threshold = thresholds.get(trigger, 0.9)
        near = [c for c in confidences if threshold - 0.15 <= c < threshold]
        notes.append(
            f"`{trigger}`: {len(confidences)} detection(s) suppressed below "
            f"threshold {threshold:.2f} (max seen {max(confidences):.2f}"
            f"{f', {len(near)} within 0.15 of the bar' if near else ''}). "
            f"Read the detail lines: if they were right, lower the threshold; "
            f"if they were noise, the threshold is doing its job."
        )

    if stats.detector_failures:
        notes.append(
            f"{len(stats.detector_failures)} detector failure(s) — detection "
            f"health is broken, fix before trusting any quiet meeting."
        )

    if stats.errors:
        notes.append(f"{len(stats.errors)} error(s) recorded — see the error list.")

    return notes or ["Nothing anomalous. Confirm the behaviour matched the role's intent."]


def format_report(rows: list[tuple[MeetingStats, dict[str, float]]]) -> str:
    """Render the calibration report as plain text."""
    if not rows:
        return "No meetings found. Run a meeting first, then calibrate.\n"

    lines: list[str] = ["Synedrios Calibration Report", "=" * 60, ""]

    totals: Counter[str] = Counter()
    for stats, _ in rows:
        totals["messages"] += stats.messages
        totals["contributions"] += stats.contributions

    lines.append(
        f"{len(rows)} meeting(s), {totals['messages']} message(s), "
        f"{totals['contributions']} contribution(s)."
    )
    lines.append("")

    for stats, thresholds in rows:
        lines.append(f"--- {stats.title}  [role: {stats.role}, {stats.status}]")
        lines.append(f"    meeting_id : {stats.meeting_id}")
        lines.append(
            f"    messages   : {stats.messages}    "
            f"contributions: {stats.contributions}    "
            f"speak rate: {stats.speak_rate:.0%}"
        )
        if stats.triggers_fired:
            fired = ", ".join(f"{k}={v}" for k, v in sorted(stats.triggers_fired.items()))
            lines.append(f"    fired      : {fired}")
        if stats.suppressed:
            supp = ", ".join(f"{k}={v}" for k, v in sorted(stats.suppressed.items()))
            lines.append(f"    suppressed : {supp}")
        if stats.throttled_by_trigger:
            thr = ", ".join(f"{k}={v}" for k, v in sorted(stats.throttled_by_trigger.items()))
            lines.append(f"    throttled  : {thr}  (what cooldown/rate-limit silenced)")
        if stats.detector_failures:
            lines.append("    detector failures:")
            for f in stats.detector_failures[:5]:
                lines.append(f"      {f}")
        if stats.outputs:
            lines.append(f"    outputs    : {', '.join(stats.outputs)}")
        # Always show the memories row — a zero is a signal (it either means
        # nothing durable was said, or extraction failed; see error lines),
        # and omitting the row hid a total extraction loss in the first
        # live rehearsal.
        lines.append(f"    memories   : {stats.memories} learned")
        if stats.below_threshold:
            lines.append("    near misses:")
            for trigger, confidence, detail in sorted(stats.below_threshold, key=lambda x: -x[1])[
                :8
            ]:
                lines.append(f"      {confidence:.2f}  {trigger}  {detail}")
        if stats.errors:
            lines.append("    errors     :")
            for err in stats.errors[:5]:
                lines.append(f"      {err}")
        lines.append("    recommendations:")
        for note in recommend(stats, thresholds):
            lines.append(f"      - {note}")
        lines.append("")

    return "\n".join(lines)


def build_rows(
    db: Database,
    meeting_id: str = "",
    role: str = "",
    limit: int = 100,
) -> list[tuple[MeetingStats, dict[str, float]]]:
    """Collect stats for the meetings matching the filters, oldest first."""
    meetings = db.list_meetings(limit=limit)
    if meeting_id:
        meetings = [m for m in meetings if m.meeting_id == meeting_id]
    if role:
        meetings = [m for m in meetings if (m.mode_config or {}).get("name") == role]
    meetings.reverse()  # oldest first reads chronologically
    return [(collect(db, m), thresholds_for(m)) for m in meetings]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse Synedrios audit logs and tune thresholds."
    )
    parser.add_argument("--db", default="data/synedrios.db", help="Database path.")
    parser.add_argument("--meeting", default="", help="Restrict to one meeting id.")
    parser.add_argument("--role", default="", help="Restrict to one role name.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum meetings to read.")
    parser.add_argument("--out", default="", help="Also write the report to this file.")
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.exists():
        print(f"No database at {path}. Run a meeting first.")
        return 1

    db = Database(path)
    report = format_report(build_rows(db, args.meeting, args.role, args.limit))
    print(report)
    if args.out:
        Path(args.out).write_text(report)
        print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
