"""Unit tests for the participation engine (P01-P34).

The participation model is the product, so this is the most important
suite. LLM calls are mocked via the conftest MockLLMAdapter. Covers
trigger evaluation, turn management, system-prompt and message
construction, post-meeting generation, and cross-role behaviour.
"""

from __future__ import annotations

import time

import pytest

from src.engine.modes import (
    InitiativeConfig,
    ModeConfig,
    TriggerConfig,
    load_all_modes,
)
from src.engine.participation import ParticipationEngine
from src.models import MEMORY_TYPES, EventType, MeetingEvent, TriggerResult
from tests.conftest import MockLLMAdapter

MODES = load_all_modes("config/modes")


def make_engine(
    role: str, llm: MockLLMAdapter | None = None, name: str = "Synedrios"
) -> ParticipationEngine:
    return ParticipationEngine(
        mode=MODES[role],
        llm=llm or MockLLMAdapter(),
        participant_name=name,
        participant_id="p1",
        meeting_id="m1",
    )


def ev(content: str, speaker: str = "Nikos") -> MeetingEvent:
    return MeetingEvent(event_type=EventType.CHAT, speaker=speaker, content=content)


# --------------------------------------------------------------------------
# Trigger evaluation (P01-P08)
# --------------------------------------------------------------------------


def test_p01_direct_address_by_name() -> None:
    """P01 — Addressing the AI by name fires addressed_directly."""
    engine = make_engine("secretary")
    fired = engine.evaluate_triggers(ev("Synedrios, please log that decision."), [])
    assert any(t.trigger_type == "addressed_directly" and t.fired for t in fired)


def test_p02_direct_address_by_mention() -> None:
    """P02 — An @mention fires addressed_directly."""
    engine = make_engine("secretary")
    fired = engine.evaluate_triggers(ev("@synedrios can you capture that?"), [])
    assert any(t.trigger_type == "addressed_directly" for t in fired)


def test_p03_no_name_no_trigger_at_level_1() -> None:
    """P03 — At initiative level 1, an unaddressed message does not trigger."""
    engine = make_engine("secretary")
    fired = engine.evaluate_triggers(ev("Let's move on to the next item."), [])
    assert fired == []


def test_p04_silent_mode_never_triggers() -> None:
    """P04 — Silent mode (level 0) returns no triggers for any input."""
    engine = make_engine("silent_observer")
    assert engine.evaluate_triggers(ev("Silent_observer, comment please!"), []) == []
    assert engine.evaluate_triggers(ev("anything at all"), []) == []


def test_p05_circular_fires_on_repetition(fixture_loader) -> None:
    """P05 — Circular-discussion detection fires on a repetitive transcript."""
    engine = make_engine("facilitator")
    transcript = fixture_loader("circular_meeting")
    fired = engine.evaluate_triggers(transcript[-1], transcript)
    assert any(t.trigger_type == "circular_discussion" for t in fired)


def test_p06_circular_no_false_positive() -> None:
    """P06 — Circular detection does not fire below the data/repetition threshold."""
    engine = make_engine("facilitator")
    transcript = [ev(f"Distinct point {i} on subject {i}.", f"S{i}") for i in range(6)]
    fired = engine.evaluate_triggers(transcript[-1], transcript)
    assert not any(t.trigger_type == "circular_discussion" for t in fired)


def test_p07_synthesis_fires_with_many_speakers(fixture_loader) -> None:
    """P07 — Synthesis opportunity fires when 3+ speakers are active."""
    engine = make_engine("facilitator")
    transcript = fixture_loader("long_planning")
    fired = engine.evaluate_triggers(transcript[-1], transcript)
    assert any(t.trigger_type == "synthesis_opportunity" for t in fired)


def test_p08_synthesis_needs_minimum_events(fixture_loader) -> None:
    """P08 — Synthesis does not fire with fewer than 15 events."""
    engine = make_engine("facilitator")
    transcript = fixture_loader("quick_decision")  # 8 events
    fired = engine.evaluate_triggers(transcript[-1], transcript)
    assert not any(t.trigger_type == "synthesis_opportunity" for t in fired)


# --------------------------------------------------------------------------
# Turn management (P09-P14)
# --------------------------------------------------------------------------


def test_p09_can_contribute_when_fresh() -> None:
    """P09 — can_contribute is True with no recent contributions."""
    assert make_engine("secretary").can_contribute() is True


def test_p10_cooldown_blocks() -> None:
    """P10 — can_contribute is False during the cooldown window."""
    engine = make_engine("secretary")  # cooldown 30s
    engine.record_contribution()
    assert engine.can_contribute() is False


def test_p11_rate_limit_blocks() -> None:
    """P11 — can_contribute is False once the rate limit is reached."""
    mode = ModeConfig(
        name="burst",
        initiative=InitiativeConfig(level=5, cooldown_seconds=0, max_contributions_per_10min=3),
    )
    engine = ParticipationEngine(mode=mode, llm=MockLLMAdapter())
    for _ in range(3):
        engine.record_contribution()
    assert engine.can_contribute() is False


def test_p12_record_contribution_updates_state() -> None:
    """P12 — record_contribution updates the last-contribution time and count."""
    engine = make_engine("secretary")
    assert engine._last_contribution_time == 0
    engine.record_contribution()
    assert engine._last_contribution_time > 0
    assert engine._contribution_count == 1


def test_p13_rate_limit_window_slides() -> None:
    """P13 — Contributions older than the 10-minute window expire."""
    mode = ModeConfig(
        name="burst",
        initiative=InitiativeConfig(level=5, cooldown_seconds=0, max_contributions_per_10min=3),
    )
    engine = ParticipationEngine(mode=mode, llm=MockLLMAdapter())
    # Five stale contributions, all older than 10 minutes
    engine._contributions_window = [time.time() - 700] * 5
    assert engine.can_contribute() is True
    assert engine._contributions_window == []  # stale entries pruned


def test_p14_cooldown_recovers() -> None:
    """P14 — After the cooldown elapses, can_contribute is True again."""
    engine = make_engine("secretary")  # cooldown 30s
    engine._last_contribution_time = time.time() - 31
    assert engine.can_contribute() is True


# --------------------------------------------------------------------------
# System prompt construction (P15-P21)
# --------------------------------------------------------------------------


def test_p15_prompt_includes_role_name() -> None:
    """P15 — System prompt names the role."""
    assert "Secretary" in make_engine("secretary")._build_system_prompt()


def test_p16_prompt_includes_enabled_contributions() -> None:
    """P16 — System prompt lists enabled contribution types."""
    prompt = make_engine("secretary")._build_system_prompt()
    assert "may contribute" in prompt
    assert "clarifying question" in prompt


def test_p17_prompt_includes_disabled_contributions() -> None:
    """P17 — System prompt lists disabled contribution types."""
    prompt = make_engine("secretary")._build_system_prompt()
    assert "must NOT contribute" in prompt
    assert "opinion position" in prompt


def test_p18_prompt_includes_tone() -> None:
    """P18 — System prompt includes a tone description."""
    assert "Tone:" in make_engine("secretary")._build_system_prompt()


def test_p19_prompt_includes_authority() -> None:
    """P19 — System prompt states the authority level."""
    assert "Authority level: observer" in make_engine("secretary")._build_system_prompt()
    assert "Authority level: chair" in make_engine("facilitator")._build_system_prompt()


def test_p20_prompt_includes_word_limit() -> None:
    """P20 — System prompt states the contribution word limit."""
    assert "under 60 words" in make_engine("secretary")._build_system_prompt()


def test_p21_prompt_includes_memories() -> None:
    """P21 — Provided memories are injected into the system prompt."""
    prompt = make_engine("secretary")._build_system_prompt(
        memories=["Sarah prefers written proposals over verbal pitches."]
    )
    assert "PREVIOUS meetings" in prompt
    assert "Sarah prefers written proposals" in prompt
    # Temporal grounding (T6 live-rehearsal finding): remembered facts must be
    # attributed as prior knowledge, never as something said in this meeting.
    assert "prior knowledge" in prompt


# --------------------------------------------------------------------------
# Message construction (P22-P24)
# --------------------------------------------------------------------------


def _messages_for(engine: ParticipationEngine) -> str:
    trigger = TriggerResult(
        trigger_type="addressed_directly", fired=True, priority=1, reason="Addressed by Nikos"
    )
    event = ev("Synedrios, what's the status?")
    transcript = [ev("We kicked off the review.", "Sarah"), event]
    msgs = engine._build_messages(trigger, event, transcript)
    return msgs[0]["content"]


def test_p22_messages_include_transcript() -> None:
    """P22 — Built messages include recent transcript context."""
    assert "We kicked off the review." in _messages_for(make_engine("secretary"))


def test_p23_messages_include_trigger() -> None:
    """P23 — Built messages include the trigger type and reason."""
    content = _messages_for(make_engine("secretary"))
    assert "addressed_directly" in content
    assert "Addressed by Nikos" in content


def test_p24_messages_include_latest_event() -> None:
    """P24 — Built messages include the latest event content."""
    assert "what's the status?" in _messages_for(make_engine("secretary"))


# --------------------------------------------------------------------------
# Post-meeting generation (P25-P29)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p25_post_meeting_calls_llm_per_output(fixture_loader) -> None:
    """P25 — generate_post_meeting issues one LLM call per output type."""
    llm = MockLLMAdapter("output text")
    engine = make_engine("secretary", llm)
    transcript = fixture_loader("simple_standup")
    outputs = await engine.generate_post_meeting(transcript, "Standup")
    expected = MODES["secretary"].post_meeting.outputs
    assert len(llm.calls) == len(expected)
    assert set(outputs.keys()) == set(expected)


@pytest.mark.asyncio
async def test_p26_secretary_outputs(fixture_loader) -> None:
    """P26 — Secretary produces notes, summary, actions, and follow-up."""
    engine = make_engine("secretary", MockLLMAdapter("x"))
    outputs = await engine.generate_post_meeting(fixture_loader("simple_standup"), "Standup")
    assert {
        "structured_notes",
        "executive_summary",
        "action_items_by_owner",
        "follow_up_agenda",
    } <= set(outputs)


@pytest.mark.asyncio
async def test_p27_silent_observer_full_suite(fixture_loader) -> None:
    """P27 — Silent observer produces the full comprehensive output suite."""
    engine = make_engine("silent_observer", MockLLMAdapter("x"))
    outputs = await engine.generate_post_meeting(fixture_loader("long_planning"), "Planning")
    assert {
        "structured_notes",
        "executive_summary",
        "action_items_by_owner",
        "decision_log",
        "risk_register",
        "follow_up_agenda",
    } <= set(outputs)


@pytest.mark.asyncio
async def test_p28_output_prompt_includes_title(fixture_loader) -> None:
    """P28 — Output prompts include the meeting title."""
    llm = MockLLMAdapter("x")
    engine = make_engine("secretary", llm)
    await engine.generate_post_meeting(fixture_loader("quick_decision"), "DB Decision")
    assert all("DB Decision" in c["messages"][0]["content"] for c in llm.calls)


@pytest.mark.asyncio
async def test_p29_output_prompt_includes_transcript(fixture_loader) -> None:
    """P29 — Output prompts include the formatted transcript."""
    llm = MockLLMAdapter("x")
    engine = make_engine("secretary", llm)
    await engine.generate_post_meeting(fixture_loader("quick_decision"), "DB Decision")
    # A distinctive phrase from the quick_decision fixture must reach the prompt
    assert any("PostgreSQL" in c["messages"][0]["content"] for c in llm.calls)


# --------------------------------------------------------------------------
# Cross-role behaviour (P30-P34)
# --------------------------------------------------------------------------


def test_p30_secretary_only_responds_when_addressed() -> None:
    """P30 — Secretary (level 1) triggers only on direct address."""
    engine = make_engine("secretary")
    assert engine.evaluate_triggers(ev("The build is green."), []) == []
    addressed = engine.evaluate_triggers(ev("Synedrios, note the build status."), [])
    assert any(t.trigger_type == "addressed_directly" for t in addressed)


def test_p31_technical_advisor_reactive_to_questions() -> None:
    """P31 — Technical advisor (level 4) reacts to open questions.

    factual_error and contradiction triggers are deferred to LLM detection
    (T2); the reactive behaviour exercised here is the level-4 open-question
    path that secretary (level 1) does not take. It reports as `open_question`,
    not `addressed_directly` — nobody addressed the AI, and the audit log has
    to say which of the two actually happened (T6).
    """
    advisor = make_engine("technical_advisor")
    fired = advisor.evaluate_triggers(ev("Is sharding the right call here?"), [])
    assert any(t.trigger_type == "open_question" for t in fired)
    assert not any(t.trigger_type == "addressed_directly" for t in fired)
    # Secretary (level 1) must NOT react to the same open question
    assert (
        make_engine("secretary").evaluate_triggers(ev("Is sharding the right call here?"), []) == []
    )


def test_p32_facilitator_participates_more_than_secretary() -> None:
    """P32 — Facilitator is configured for lead behaviour, secretary is not.

    Revised in T6. The original assertion compared raw cooldown and rate-limit
    numbers, which turned out to measure the wrong thing: the secretary's
    throttles never bind (at initiative level 1 it speaks only when addressed),
    while the facilitator's bound on every message. Rehearsal showed the
    facilitator's speaking rate was being set by its rate limiter rather than
    by its triggers, so the throttles were tightened; what makes it a lead role
    is its initiative level and its trigger set, which is what this now asserts.
    """
    fac = MODES["facilitator"]
    sec = MODES["secretary"]
    assert fac.initiative.level > sec.initiative.level
    assert len(fac.initiative.triggers) > len(sec.initiative.triggers)

    # It must still be throttled: a chair that can speak on every message is
    # not chairing, and the limiter should not be the only thing stopping it.
    assert fac.initiative.cooldown_seconds > 0
    assert fac.initiative.max_contributions_per_10min <= 6


def test_p33_silent_observer_never_triggers(fixture_loader) -> None:
    """P33 — Silent observer never triggers, even when addressed or in a loop."""
    engine = make_engine("silent_observer")
    assert engine.evaluate_triggers(ev("Silent_observer, weigh in now."), []) == []
    transcript = fixture_loader("circular_meeting")
    assert engine.evaluate_triggers(transcript[-1], transcript) == []


def test_p34_coach_silent_like_observer() -> None:
    """P34 — Coach is silent during the meeting, the same as the observer."""
    engine = make_engine("coach")
    assert engine.evaluate_triggers(ev("Coach, any feedback right now?"), []) == []


# --------------------------------------------------------------------------
# LLM-based detection: factual_error and contradiction (P35-P44, T2)
# LLM calls are mocked — these verify the wiring, pre-filter, threshold
# gating, and JSON handling. Real detection quality is checked by the
# integration suite (I10-I12).
# --------------------------------------------------------------------------


def _fe_mode(threshold: float = 0.9) -> ModeConfig:
    return ModeConfig(
        name="fe",
        initiative=InitiativeConfig(
            level=4,
            triggers=[
                TriggerConfig(type="factual_error", priority=2, confidence_threshold=threshold)
            ],
        ),
    )


def _contra_mode(threshold: float = 0.8) -> ModeConfig:
    return ModeConfig(
        name="co",
        initiative=InitiativeConfig(
            level=4,
            triggers=[
                TriggerConfig(type="contradiction", priority=3, confidence_threshold=threshold)
            ],
        ),
    )


def _llm_engine(mode: ModeConfig, response: str) -> tuple[ParticipationEngine, MockLLMAdapter]:
    llm = MockLLMAdapter(response)
    return ParticipationEngine(mode=mode, llm=llm), llm


def test_p35_prefilter_distinguishes_claims() -> None:
    """P35 — The assertive pre-filter passes claims and rejects non-claims."""
    engine = make_engine("technical_advisor")
    assert engine._looks_assertive("Redis is fundamentally a relational database.")
    assert engine._looks_assertive("The API rate limit is 100 requests per second.")
    # Interior proper noun (a named technology) is enough of a claim signal,
    # even without a number or copula — matches the technical_debate fixture.
    assert engine._looks_assertive(
        "Honestly, Python threads run truly in parallel across separate cores."
    )
    assert not engine._looks_assertive("Thanks all.")
    assert not engine._looks_assertive("No blockers.")
    assert not engine._looks_assertive("Sounds good to me.")


@pytest.mark.asyncio
async def test_p36_factual_error_fires_above_threshold() -> None:
    """P36 — A high-confidence detected error fires the trigger."""
    engine, llm = _llm_engine(
        _fe_mode(0.9),
        (
            '{"error": true, "is_opinion": false, "confidence": 0.95, '
            '"correction": "Redis is a key-value store, not relational."}'
        ),
    )
    results = await engine.evaluate_llm_triggers(
        ev("Redis is fundamentally a relational database."), []
    )
    assert len(results) == 1
    assert results[0].trigger_type == "factual_error"
    assert results[0].fired is True
    assert results[0].confidence == 0.95
    assert "key-value" in results[0].reason
    assert len(llm.calls) == 1  # one detection call


@pytest.mark.asyncio
async def test_p37_factual_error_below_threshold_not_fired() -> None:
    """P37 — A low-confidence detection is returned but not fired."""
    engine, _ = _llm_engine(
        _fe_mode(0.9),
        ('{"error": true, "is_opinion": false, "confidence": 0.6, "correction": "maybe"}'),
    )
    results = await engine.evaluate_llm_triggers(
        ev("MongoDB is ACID-compliant across documents by default."), []
    )
    assert len(results) == 1
    assert results[0].fired is False
    assert results[0].confidence == 0.6


@pytest.mark.asyncio
async def test_p38_factual_error_opinion_filtered() -> None:
    """P38 — A statement judged to be opinion never fires."""
    engine, _ = _llm_engine(
        _fe_mode(0.9),
        ('{"error": true, "is_opinion": true, "confidence": 0.99, "correction": "x"}'),
    )
    results = await engine.evaluate_llm_triggers(
        ev("MongoDB is a much better database than PostgreSQL."), []
    )
    assert results == []


@pytest.mark.asyncio
async def test_p39_prefilter_blocks_llm_call() -> None:
    """P39 — A non-assertive message never reaches the LLM."""
    engine, llm = _llm_engine(_fe_mode(0.9), '{"error": true, "confidence": 0.99}')
    results = await engine.evaluate_llm_triggers(ev("Thanks, sounds good."), [])
    assert results == []
    assert llm.calls == []  # pre-filter short-circuited before any call


@pytest.mark.asyncio
async def test_p40_contradiction_fires_with_prior() -> None:
    """P40 — A detected reversal of an earlier statement fires."""
    engine, llm = _llm_engine(
        _contra_mode(0.8),
        (
            '{"contradiction": true, "confidence": 0.9, '
            '"earlier_statement": "100 rps", "explanation": "rate limit reversed"}'
        ),
    )
    transcript = [
        ev("The API rate limit is 100 requests per second.", "Dana"),
        ev("Got it, plenty of headroom.", "Ken"),
        ev("Actually the API rate limit is only 20 requests per second.", "Dana"),
    ]
    results = await engine.evaluate_llm_triggers(transcript[-1], transcript)
    assert len(results) == 1
    assert results[0].trigger_type == "contradiction"
    assert results[0].fired is True
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_p41_contradiction_needs_two_prior() -> None:
    """P41 — With fewer than two prior statements, no contradiction call is made."""
    engine, llm = _llm_engine(_contra_mode(0.8), '{"contradiction": true, "confidence": 0.99}')
    latest = ev("Actually the API rate limit is only 20 requests per second.", "Dana")
    results = await engine.evaluate_llm_triggers(latest, [latest])
    assert results == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_p42_no_llm_triggers_for_secretary() -> None:
    """P42 — A role without LLM triggers issues no detection calls."""
    llm = MockLLMAdapter('{"error": true, "confidence": 0.99}')
    engine = ParticipationEngine(mode=MODES["secretary"], llm=llm)
    results = await engine.evaluate_llm_triggers(
        ev("Redis is fundamentally a relational database."), []
    )
    assert results == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_p43_silent_role_skips_detection() -> None:
    """P43 — A silent (level 0) role runs no LLM detection even if configured."""
    mode = ModeConfig(
        name="silent_fe",
        initiative=InitiativeConfig(level=0, triggers=[TriggerConfig(type="factual_error")]),
    )
    llm = MockLLMAdapter('{"error": true, "confidence": 0.99}')
    engine = ParticipationEngine(mode=mode, llm=llm)
    results = await engine.evaluate_llm_triggers(
        ev("Redis is fundamentally a relational database."), []
    )
    assert results == []
    assert llm.calls == []


def test_p44_parse_json_object_tolerant() -> None:
    """P44 — JSON extraction tolerates code fences and surrounding prose."""
    parse = ParticipationEngine._parse_json_object
    assert parse('```json\n{"error": false}\n```') == {"error": False}
    assert parse('Sure — {"error": true, "confidence": 0.9} hope that helps') == {
        "error": True,
        "confidence": 0.9,
    }
    assert parse("no json here at all") is None
    assert parse("") is None


# --------------------------------------------------------------------------
# Memory-writing extraction and injection (P45-P51, T3)
# LLM calls are mocked. Real extraction quality / cross-meeting recall is
# checked by the integration suite (I13).
# --------------------------------------------------------------------------


def _mem_response(*memories: dict) -> str:
    import json

    return json.dumps({"memories": list(memories)})


@pytest.mark.asyncio
async def test_p45_extract_parses_typed_memories() -> None:
    """P45 — extract_memories returns typed, confidence-bearing memories."""
    engine, _ = _llm_engine(
        MODES["secretary"],
        _mem_response(
            {
                "type": "decision",
                "content": "Chose PostgreSQL for the orders service.",
                "confidence": 0.95,
                "status": "new",
            },
            {
                "type": "preference",
                "content": "Sarah prefers written proposals.",
                "confidence": 0.8,
            },
        ),
    )
    results = await engine.extract_memories([ev("We chose PostgreSQL.")], [])
    assert [m.memory_type for m in results] == ["decision", "preference"]
    assert results[0].confidence == 0.95
    assert all(m.memory_type in MEMORY_TYPES for m in results)


@pytest.mark.asyncio
async def test_p46_extract_empty_transcript_no_call() -> None:
    """P46 — No transcript means no extraction call at all."""
    engine, llm = _llm_engine(
        MODES["secretary"], _mem_response({"type": "fact", "content": "x", "confidence": 0.9})
    )
    results = await engine.extract_memories([], ["existing"])
    assert results == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_p47_extract_filters_types_and_caps() -> None:
    """P47 — Invalid types are dropped and the batch is capped at the maximum."""
    candidates = [
        {"type": "fact", "content": f"Durable fact number {i}.", "confidence": 0.9}
        for i in range(12)
    ]
    candidates.append({"type": "todo", "content": "not a real memory type", "confidence": 0.9})
    engine, _ = _llm_engine(MODES["secretary"], _mem_response(*candidates))
    results = await engine.extract_memories([ev("lots happened")], [])
    assert len(results) == 10  # MAX_MEMORIES_PER_MEETING
    assert all(m.memory_type in MEMORY_TYPES for m in results)


@pytest.mark.asyncio
async def test_p48_extract_dedupes_against_existing() -> None:
    """P48 — A candidate matching an existing memory (exact) is dropped."""
    engine, _ = _llm_engine(
        MODES["secretary"],
        _mem_response(
            {"type": "decision", "content": "Chose PostgreSQL.", "confidence": 0.9},
            {"type": "fact", "content": "The API limit is 20 rps.", "confidence": 0.9},
        ),
    )
    results = await engine.extract_memories([ev("recap")], existing_contents=["Chose PostgreSQL."])
    assert [m.content for m in results] == ["The API limit is 20 rps."]


@pytest.mark.asyncio
async def test_p49_extract_dedupes_within_batch() -> None:
    """P49 — Duplicate candidates within one batch collapse to one."""
    engine, _ = _llm_engine(
        MODES["secretary"],
        _mem_response(
            {"type": "fact", "content": "The budget is fixed.", "confidence": 0.9},
            {"type": "fact", "content": "The budget is fixed.", "confidence": 0.9},
        ),
    )
    results = await engine.extract_memories([ev("recap")], [])
    assert len(results) == 1


@pytest.mark.asyncio
async def test_p50_extract_preserves_update_supersession() -> None:
    """P50 — status=update and supersedes are preserved for the connector."""
    engine, _ = _llm_engine(
        MODES["secretary"],
        _mem_response(
            {
                "type": "fact",
                "content": "The API limit is 20 rps.",
                "confidence": 0.9,
                "status": "update",
                "supersedes": "The API limit is 100 rps.",
            },
        ),
    )
    results = await engine.extract_memories([ev("recap")], ["The API limit is 100 rps."])
    # The candidate is not an exact duplicate (content differs), so it survives
    assert len(results) == 1
    assert results[0].status == "update"
    assert results[0].supersedes == "The API limit is 100 rps."


@pytest.mark.asyncio
async def test_p51_generate_response_uses_injected_memory() -> None:
    """P51 — Injected cross-meeting memory reaches the response system prompt."""
    llm = MockLLMAdapter("ok")
    engine = ParticipationEngine(mode=MODES["secretary"], llm=llm)
    engine.injected_memories = ["[preference] Sarah prefers PostgreSQL."]
    trigger = TriggerResult(
        trigger_type="addressed_directly", fired=True, priority=1, reason="asked"
    )
    await engine.generate_response(trigger, ev("Synedrios, remind me?"), [ev("hi")])
    assert "Sarah prefers PostgreSQL" in llm.calls[0]["system"]
    assert "PREVIOUS meetings" in llm.calls[0]["system"]


@pytest.mark.asyncio
async def test_p52_extract_handles_malformed_output() -> None:
    """P52 — Malformed extraction output: wrong shapes degrade to no memories,
    but an UNPARSEABLE response raises so the caller can audit the failure.

    Revised in T6: the original "always return []" contract made a truncated
    response indistinguishable from an empty meeting — which silently lost
    every memory from the richest rehearsal meeting.
    """
    import json

    for shape_only in ['{"memories": "should be a list"}', json.dumps({"nothing": True})]:
        engine, _ = _llm_engine(MODES["secretary"], shape_only)
        assert await engine.extract_memories([ev("recap")], []) == []

    engine, _ = _llm_engine(MODES["secretary"], "not json at all")
    with pytest.raises(ValueError, match="unparseable"):
        await engine.extract_memories([ev("recap")], [])


# --------------------------------------------------------------------------
# Trigger refractory and open-question control (P53-P56) — added in T6
# --------------------------------------------------------------------------


def _state_trigger_mode(gap: int | None = None) -> ModeConfig:
    """A lead-level mode whose only trigger is the synthesis state trigger."""
    extra: dict = {"min_unresolved_threads": 2}
    if gap is not None:
        extra["min_messages_between"] = gap
    return ModeConfig(
        name="test_lead",
        initiative=InitiativeConfig(
            level=6,
            cooldown_seconds=0,
            max_contributions_per_10min=100,
            triggers=[TriggerConfig(type="synthesis_opportunity", priority=4, extra=extra)],
        ),
    )


def _busy_transcript(n: int = 20) -> list[MeetingEvent]:
    speakers = ["Nikos", "Sarah", "Mike", "Priya"]
    return [ev(f"Point number {i}.", speakers[i % len(speakers)]) for i in range(n)]


def test_p53_state_trigger_fires_before_any_contribution() -> None:
    """P53 — A state trigger fires normally when it has never contributed."""
    engine = ParticipationEngine(mode=_state_trigger_mode(), llm=MockLLMAdapter())
    fired = engine.evaluate_triggers(ev("And another thing."), _busy_transcript())
    assert any(t.trigger_type == "synthesis_opportunity" for t in fired)


def test_p54_state_trigger_is_refractory_after_contributing() -> None:
    """P54 — A state trigger stays quiet for min_messages_between after it speaks.

    The condition ("several speakers are active") remains true for the rest of
    the meeting, so without this the trigger fires on every subsequent message
    and the AI's speaking rate is decided by its rate limiter.
    """
    transcript = _busy_transcript()
    engine = ParticipationEngine(mode=_state_trigger_mode(gap=5), llm=MockLLMAdapter())

    assert engine.evaluate_triggers(ev("First."), transcript)
    engine.record_contribution("synthesis_opportunity")

    for i in range(4):
        assert engine.evaluate_triggers(ev(f"Quiet {i}."), transcript) == []

    assert engine.evaluate_triggers(ev("Now again."), transcript)


def test_p55_refractory_uses_the_configured_gap() -> None:
    """P55 — A role can widen or narrow its own refractory gap."""
    transcript = _busy_transcript()
    narrow = ParticipationEngine(mode=_state_trigger_mode(gap=1), llm=MockLLMAdapter())
    narrow.evaluate_triggers(ev("First."), transcript)
    narrow.record_contribution("synthesis_opportunity")
    assert narrow.evaluate_triggers(ev("Immediately after."), transcript)

    wide = ParticipationEngine(mode=_state_trigger_mode(gap=50), llm=MockLLMAdapter())
    wide.evaluate_triggers(ev("First."), transcript)
    wide.record_contribution("synthesis_opportunity")
    for i in range(10):
        assert wide.evaluate_triggers(ev(f"Still quiet {i}."), transcript) == []


def test_p56_open_questions_can_be_switched_off_per_role() -> None:
    """P56 — respond_to_open_questions gates the open-question path only.

    The facilitator chairs; it does not answer the room's subject-matter
    questions. Direct address must still always reach it.
    """
    mode = ModeConfig(
        name="chair",
        initiative=InitiativeConfig(
            level=6,
            triggers=[
                TriggerConfig(
                    type="addressed_directly",
                    priority=1,
                    extra={"respond_to_open_questions": False},
                )
            ],
        ),
    )
    engine = ParticipationEngine(mode=mode, llm=MockLLMAdapter(), participant_name="Synedrios")

    assert engine.evaluate_triggers(ev("Should we shard the database?"), []) == []

    addressed = engine.evaluate_triggers(ev("Synedrios, where are we on time?"), [])
    assert any(t.trigger_type == "addressed_directly" for t in addressed)

    # The facilitator config ships with the gate closed.
    assert MODES["facilitator"].initiative.triggers[0].extra["respond_to_open_questions"] is False


# --------------------------------------------------------------------------
# Questions put to a colleague (P57-P61)
# --------------------------------------------------------------------------


def _room() -> list[MeetingEvent]:
    """A short transcript establishing who is in the meeting."""
    return [
        ev("Morning all.", "Nikos"),
        ev("Migration is nearly done.", "Sarah Chen"),
        ev("Caching layer shipped.", "Mike"),
    ]


def test_p57_question_mentioning_a_colleague_is_not_answered() -> None:
    """P57 — An @-mentioned colleague's question does not draw the AI in.

    The connector resolves the mention to a name in metadata["directed_at"];
    the question belongs to that colleague.
    """
    advisor = make_engine("technical_advisor")
    question = MeetingEvent(
        event_type=EventType.CHAT,
        speaker="Nikos",
        content="Sarah Chen can you confirm the index is rebuilt?",
        metadata={"directed_at": ["Sarah Chen"]},
    )
    assert advisor.evaluate_triggers(question, _room()) == []


def test_p58_question_opening_with_a_name_is_not_answered() -> None:
    """P58 — "Mike, can you…?" is Mike's question, not the AI's."""
    advisor = make_engine("technical_advisor")
    for text in (
        "Mike, can you take the metrics work?",
        "Mike: is staging back up?",
        "Sarah, what did the profiler say?",
        # No punctuation after the name, which is how most people type it.
        # This is why the rule keys on the opening word rather than on a
        # comma, and why it accepts the occasional false positive.
        "Mike is staging back up?",
    ):
        assert advisor.evaluate_triggers(ev(text, "Nikos"), _room()) == [], text


def test_p59_genuine_room_question_still_fires() -> None:
    """P59 — A question with nobody's name on it still reaches the AI."""
    advisor = make_engine("technical_advisor")
    fired = advisor.evaluate_triggers(ev("Is sharding the right call here?", "Nikos"), _room())
    assert any(t.trigger_type == "open_question" for t in fired)


def test_p60_direct_address_survives_a_colleagues_name() -> None:
    """P60 — Being named still reaches the AI even alongside a colleague.

    Direct address is checked before the directed-question rule, so
    "Synedrios, do you agree with Mike?" is answered.
    """
    advisor = make_engine("technical_advisor")
    addressed = MeetingEvent(
        event_type=EventType.CHAT,
        speaker="Nikos",
        content="Synedrios, do you agree with Mike?",
        metadata={"directed_at": ["Mike"]},
    )
    fired = advisor.evaluate_triggers(addressed, _room())
    assert any(t.trigger_type == "addressed_directly" and t.fired for t in fired)


def test_p61_a_name_that_is_not_in_the_meeting_does_not_silence_it() -> None:
    """P61 — Only actual participants count as addressees.

    "Postgres, is that the bottleneck?" reads oddly but must not be mistaken
    for addressing someone; the rule keys on who has spoken in this meeting.
    """
    advisor = make_engine("technical_advisor")
    fired = advisor.evaluate_triggers(ev("Postgres is the bottleneck, right?", "Nikos"), _room())
    assert any(t.trigger_type == "open_question" for t in fired)


# --------------------------------------------------------------------------
# Live-rehearsal fixes (P62-P65) — added in T6, Session 9
# --------------------------------------------------------------------------


def test_p62_mid_sentence_vocative_is_directed() -> None:
    """P62 — "Let's keep it civil. Mike, what's the number?" is Mike's question.

    The live rehearsal caught the AI answering exactly this shape over Mike's
    shoulder — the vocative was mid-message, so the opening-word rule missed
    it. A later sentence opening with "Name," or "Name:" now counts.
    """
    advisor = make_engine("technical_advisor")
    room = _room()
    assert (
        advisor.evaluate_triggers(
            ev("Let's keep it civil. Mike, what's the actual write throughput?"), room
        )
        == []
    )
    assert advisor.evaluate_triggers(ev("Quick check! Sarah: is the index rebuilt?"), room) == []
    # Mentioning a name mid-sentence WITHOUT vocative punctuation stays open.
    fired = advisor.evaluate_triggers(
        ev("I think Mike is right. Should we still shard the database?"), room
    )
    assert any(t.trigger_type == "open_question" for t in fired)


@pytest.mark.asyncio
async def test_p63_unparseable_detector_response_is_a_failure_result() -> None:
    """P63 — A detector that returns garbage yields an auditable failure.

    Before this, unparseable JSON returned None — indistinguishable from "no
    error found", so a broken detector looked like a quiet meeting.
    """
    engine, _ = _llm_engine(MODES["technical_advisor"], "I refuse to answer in JSON")
    results = await engine.evaluate_llm_triggers(
        ev("MongoDB is fully ACID compliant across 5 documents."), []
    )
    failed = [r for r in results if r.context.get("detector_failed")]
    assert failed, "expected detector-failure results"
    for r in failed:
        assert r.fired is False
        assert "raw_head" in r.context


@pytest.mark.asyncio
async def test_p64_truncated_memory_array_is_salvaged() -> None:
    """P64 — Complete items survive a response truncated mid-array."""
    truncated = (
        '{"memories": ['
        '{"type": "decision", "content": "PostgreSQL chosen.", "confidence": 0.9, '
        '"status": "new", "supersedes": ""}, '
        '{"type": "person", "content": "Sarah owns the decision record.", '
        '"confidence": 0.8, "status": "new", "supersedes": ""}, '
        '{"type": "fact", "content": "This one was cut of'
    )
    engine, _ = _llm_engine(MODES["secretary"], truncated)
    memories = await engine.extract_memories([ev("recap")], [])
    assert [m.content for m in memories] == [
        "PostgreSQL chosen.",
        "Sarah owns the decision record.",
    ]


@pytest.mark.asyncio
async def test_p65_relative_deadlines_are_excluded_from_memory_prompt() -> None:
    """P65 — The extraction prompt forbids relative-deadline status items."""
    engine, llm = _llm_engine(MODES["secretary"], '{"memories": []}')
    await engine.extract_memories([ev("recap")], [])
    system = llm.calls[0]["system"]
    assert "by Thursday" in system  # the worked example
    assert "transient" in system
