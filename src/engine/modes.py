"""Meeting mode configuration.

Loads role definitions from YAML files. Each role is a named preset
of behaviours across all dimensions (initiative, contributions,
attention, authority, tone, post-meeting outputs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ToneConfig(BaseModel):
    model_config = {"populate_by_name": True}
    formality: int = Field(2, alias="register")  # 1=casual, 2=professional, 3=formal, 4=ceremonial
    directness: int = 2  # 1=blunt, 2=direct, 3=diplomatic, 4=indirect
    warmth: int = 2  # 1=reserved, 2=neutral, 3=warm, 4=relational
    hedging: int = 2  # 1=assertive, 2=measured, 3=cautious
    hierarchy_awareness: int = 1  # 1=flat, 2=aware, 3=deferential


class TriggerConfig(BaseModel):
    type: str  # "addressed_directly", "factual_error", etc.
    priority: int = 5
    confidence_threshold: float = 0.8
    extra: dict[str, Any] = Field(default_factory=dict)


class InitiativeConfig(BaseModel):
    level: int = 1
    cooldown_seconds: int = 60
    max_contributions_per_10min: int = 3
    contribution_length_words: int = 80
    triggers: list[TriggerConfig] = Field(default_factory=list)


class PostMeetingConfig(BaseModel):
    outputs: list[str] = Field(
        default_factory=lambda: [
            "structured_notes",
            "action_items_by_owner",
        ]
    )
    format: str = "markdown"


class ModeConfig(BaseModel):
    """Complete meeting mode configuration."""

    name: str = "secretary"
    description: str = ""
    initiative: InitiativeConfig = Field(default_factory=InitiativeConfig)
    contributions_enabled: list[str] = Field(
        default_factory=lambda: [
            "clarifying_question",
            "decision_capture",
            "ambiguity_flag",
            "time_check",
        ]
    )
    contributions_disabled: list[str] = Field(default_factory=list)
    attention_enabled: list[str] = Field(
        default_factory=lambda: [
            "decisions",
            "action_items",
            "agenda_tracking",
            "open_questions",
            "references",
        ]
    )
    authority: int = 1
    tone: ToneConfig = Field(default_factory=ToneConfig)
    post_meeting: PostMeetingConfig = Field(default_factory=PostMeetingConfig)
    introduction_template: str = ""
    personality_note: str = ""


def load_mode(path: Path | str) -> ModeConfig:
    """Load a meeting mode from a YAML file."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    initiative_raw = raw.get("initiative", {})
    triggers = [TriggerConfig(**t) for t in initiative_raw.get("triggers", [])]

    return ModeConfig(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        initiative=InitiativeConfig(
            level=initiative_raw.get("level", 1),
            cooldown_seconds=initiative_raw.get("cooldown_seconds", 60),
            max_contributions_per_10min=initiative_raw.get("max_contributions_per_10min", 3),
            contribution_length_words=initiative_raw.get("contribution_length_words", 80),
            triggers=triggers,
        ),
        contributions_enabled=raw.get("contributions_enabled", []),
        contributions_disabled=raw.get("contributions_disabled", []),
        attention_enabled=raw.get("attention_enabled", []),
        authority=raw.get("authority", 1),
        tone=ToneConfig(**raw.get("tone", {})),
        post_meeting=PostMeetingConfig(**raw.get("post_meeting", {})),
        introduction_template=raw.get("introduction_template", ""),
        personality_note=raw.get("personality_note", ""),
    )


def load_all_modes(config_dir: Path | str = "config/modes") -> dict[str, ModeConfig]:
    """Load all YAML mode files from a directory."""
    config_dir = Path(config_dir)
    modes: dict[str, ModeConfig] = {}
    if not config_dir.exists():
        return modes
    for path in sorted(config_dir.glob("*.yml")):
        try:
            mode = load_mode(path)
            modes[mode.name] = mode
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("Failed to load mode %s: %s", path, exc)
    return modes


def generate_introduction(mode: ModeConfig, participant_name: str) -> str:
    """Generate the meeting introduction from mode config."""
    if mode.introduction_template:
        return mode.introduction_template.replace("{name}", participant_name)

    # Auto-generate from config
    parts = [f"I'm {participant_name}, joining as {mode.name.replace('_', ' ')}."]

    level = mode.initiative.level
    if level == 0:
        parts.append(
            "I won't contribute during the meeting — I'll observe silently "
            "and produce outputs afterward."
        )
    elif level == 1:
        parts.append("I'll only speak when directly addressed.")
    elif level <= 3:
        parts.append("I'll flag when I have something to contribute and wait for acknowledgement.")
    elif level == 4:
        trigger_types = [t.type.replace("_", " ") for t in mode.initiative.triggers]
        if trigger_types:
            parts.append(f"I'll speak up if I detect: {', '.join(trigger_types)}.")
        else:
            parts.append("I'll contribute when specific triggers arise.")
    elif level == 5:
        parts.append("I'll participate actively as an equal contributor.")
    elif level == 6:
        parts.append("I'll be chairing this meeting — managing agenda, time, and discussion flow.")

    outputs = mode.post_meeting.outputs
    if outputs:
        readable = [o.replace("_", " ") for o in outputs]
        parts.append(f"After the meeting I'll produce: {', '.join(readable)}.")

    return " ".join(parts)
