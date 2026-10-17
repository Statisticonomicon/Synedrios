"""Unit tests for mode configuration loading (C01-C12).

Covers YAML parsing for all five built-in roles, role-specific values,
introduction generation, template substitution, graceful error handling,
tone defaults, and directory discovery.
"""

from __future__ import annotations

from pathlib import Path

from src.engine.modes import (
    ModeConfig,
    ToneConfig,
    generate_introduction,
    load_all_modes,
)

CONFIG_DIR = "config/modes"


def test_c01_load_all_five_configs() -> None:
    """C01 — Load each of the 5 YAML configs successfully."""
    modes = load_all_modes(CONFIG_DIR)
    for name in ("secretary", "technical_advisor", "facilitator", "silent_observer", "coach"):
        assert name in modes, f"missing role: {name}"
        assert isinstance(modes[name], ModeConfig)


def test_c02_secretary_defaults() -> None:
    """C02 — Secretary: initiative level 1, authority 1."""
    mode = load_all_modes(CONFIG_DIR)["secretary"]
    assert mode.initiative.level == 1
    assert mode.authority == 1


def test_c03_technical_advisor_triggers() -> None:
    """C03 — Technical Advisor: initiative 4, has the factual_error trigger."""
    mode = load_all_modes(CONFIG_DIR)["technical_advisor"]
    assert mode.initiative.level == 4
    trigger_types = {t.type for t in mode.initiative.triggers}
    assert "factual_error" in trigger_types
    assert "contradiction" in trigger_types
    # factual_error carries an elevated confidence threshold (0.80 per D17,
    # lowered from 0.90 after live rehearsal suppressed a real error at 0.85)
    fe = next(t for t in mode.initiative.triggers if t.type == "factual_error")
    assert fe.confidence_threshold == 0.8


def test_c04_facilitator_lead_chair() -> None:
    """C04 — Facilitator: initiative 6 (lead), authority 5 (chair)."""
    mode = load_all_modes(CONFIG_DIR)["facilitator"]
    assert mode.initiative.level == 6
    assert mode.authority == 5


def test_c05_silent_observer_silent() -> None:
    """C05 — Silent Observer: initiative 0, no triggers."""
    mode = load_all_modes(CONFIG_DIR)["silent_observer"]
    assert mode.initiative.level == 0
    assert mode.initiative.triggers == []


def test_c06_coach_silent_but_expert() -> None:
    """C06 — Coach: initiative 0, authority 4."""
    mode = load_all_modes(CONFIG_DIR)["coach"]
    assert mode.initiative.level == 0
    assert mode.authority == 4


def test_c07_introduction_non_empty_all_modes() -> None:
    """C07 — generate_introduction produces non-empty text for all modes."""
    modes = load_all_modes(CONFIG_DIR)
    for name, mode in modes.items():
        intro = generate_introduction(mode, "Synedrios")
        assert intro and intro.strip(), f"empty introduction for {name}"
        assert "Synedrios" in intro


def test_c08_custom_template_name_substitution() -> None:
    """C08 — Custom introduction template uses {name} substitution."""
    mode = ModeConfig(
        name="secretary",
        introduction_template="Hi, I'm {name} and I take notes.",
    )
    intro = generate_introduction(mode, "Aria")
    assert intro == "Hi, I'm Aria and I take notes."
    assert "{name}" not in intro


def test_c09_invalid_yaml_missing_fields_graceful(tmp_path: Path) -> None:
    """C09 — Loading a malformed mode is skipped gracefully, not crashed."""
    bad = tmp_path / "broken.yml"
    # A YAML list where a mapping is expected — load_mode will raise,
    # load_all_modes must swallow it and continue.
    bad.write_text("- this is not a mode mapping\n- just a list\n")
    good = tmp_path / "ok.yml"
    good.write_text("name: minimal\n")
    modes = load_all_modes(tmp_path)
    assert "minimal" in modes
    assert "broken" not in modes


def test_c10_empty_directory_returns_empty(tmp_path: Path) -> None:
    """C10 — Loading from an empty/non-existent directory returns an empty dict."""
    assert load_all_modes(tmp_path) == {}
    assert load_all_modes(tmp_path / "does_not_exist") == {}


def test_c11_tone_defaults() -> None:
    """C11 — Tone config dimensions default sensibly (2, except hierarchy 1)."""
    tone = ToneConfig()
    assert tone.formality == 2
    assert tone.directness == 2
    assert tone.warmth == 2
    assert tone.hedging == 2
    assert tone.hierarchy_awareness == 1
    # The `register` alias populates formality
    assert ToneConfig(register=3).formality == 3


def test_c12_discovery_loads_exactly_five() -> None:
    """C12 — load_all_modes discovers exactly 5 modes from config/modes/."""
    modes = load_all_modes(CONFIG_DIR)
    assert len(modes) == 5
