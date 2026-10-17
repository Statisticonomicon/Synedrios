# T6 Field Test Protocol

How to run the real-world test, what to watch for, and where to write down what
happened. Setup instructions are in `docs/slack-setup.md`; this file is the
experiment.

**Status:** the software side is ready and rehearsed. The live side needs a
Slack workspace and real people, so it is the owner's to run.

---

## What T6 Is Actually Testing

Not whether the code runs — rehearsals cover that. T6 tests one claim: **that
the participation model produces an AI which speaks at the right moments and
says useful things**. Everything else in the system exists to serve that claim,
and nothing before this point has tested it against real human conversation.

The two failure modes to watch for, in order of how likely they are:

1. **It speaks too often, or at the wrong moments.** An over-eager participant
   is worse than a silent one — people mute it and stop inviting it.
2. **Its post-meeting documents are wrong.** Misattributed action items and
   invented decisions destroy trust faster than a missing document.

---

## Before Each Session

```bash
.venv/bin/python -m tools.preflight     # must show READY
```

Then note which meeting, which role, roughly how many people, and whether they
know the AI is present. Tell them — an unannounced participant is both an ethics
problem and a confound.

---

## The Runs

Five roles, five meetings, in this order. The order matters: the silent roles
come first so that the first real meeting cannot be disrupted by the AI.

| # | Role | Meeting to use | What it must do | What would fail it |
|---|---|---|---|---|
| 1 | `silent_observer` | Any real meeting | Say nothing at all during the meeting; produce full outputs after | Any live message |
| 2 | `coach` | A meeting with uneven participation | Say nothing live; useful feedback afterward | Any live message; generic feedback |
| 3 | `secretary` | A standup or status meeting | Stay quiet unless asked; accurate notes and action items | Speaking unprompted; wrong owners on action items |
| 4 | `technical_advisor` | An architecture or design discussion | Answer when addressed; correct real errors; stay out otherwise | Correcting opinions; nagging; missing an obvious error |
| 5 | `facilitator` | A discussion likely to circle | Manage flow without dominating | Interjecting every few messages; answering subject questions |

Run each meeting to its natural end, then `@Synedrios end meeting` and let the
post-meeting documents generate before anyone leaves.

---

## Meeting Scenarios For Friends (The Playbook)

The owner is running T6 with friends outside work. These five scenarios produce
the best calibration data a living room can: one continuous story so
cross-meeting memory gets tested for real, and a private "answer key" so the
AI can be scored against ground truth afterwards.

**The arc:** all five meetings are about ONE real, evolving plan — a weekend
trip, a dinner party, a birthday. Real stakes produce natural decisions,
owners, and deadlines, and by meeting 5 the AI should be recalling what was
agreed in meeting 1 as prior knowledge. That recall is the product's moat;
this is its first real test.

**Keep a private answer key** (a note the AI never sees): the planted errors,
the planned contradiction, who was briefed to dominate. Scoring the AI
against it afterwards is what turns five chats into data.

| # | Role | Scenario | What it measures |
|---|------|----------|------------------|
| 1 | silent_observer | **Kick-off**: 3+ people genuinely plan the thing — budget, dates, who books what | Perfect silence; accuracy of notes/decisions/action items; memory extraction quality |
| 2 | coach | **Debrief with friction**: privately brief one friend to dominate — interrupt, dismiss ideas, be mildly rude (with their consent); another goes quiet | Coaching feedback specificity + fairness; whether the abusive-pattern memory (D18) is recorded accurately and proportionately |
| 3 | secretary | **Status round** on the tasks from meeting 1. Once, mid-meeting: "Synedrios, what did we agree about X?" | Respond-only discipline; live cross-meeting recall; action-item accuracy; no transient-status memories |
| 4 | technical_advisor | **The debate with planted errors**: any factual domain the group knows (cooking science, geography, fitness, money). Privately give ONE friend 4–6 confident falsehoods to assert naturally, from blatant to subtle, plus one self-contradiction (assert X early, reverse it later). Others voice strong OPINIONS that are NOT errors ("renting is always smarter") | Precision AND recall at the new 0.80 threshold; opinion-vs-error discrimination; contradiction detection; D15 (questions aimed at a named person must draw no reply) |
| 5 | facilitator | **The unresolvable choice**: a contested decision with no right answer; two friends hold opposing preferences stubbornly and rehash | Circular-discussion timing; synthesis quality; chairs without taking sides; decision log doesn't fabricate consensus |

**Cross-cutting, every meeting:**
- Same cast throughout (person memories accumulate); one guest who appears
  only once is a bonus test.
- At least one direct address ("Synedrios, …") — it must always answer.
- At least one question aimed at a named person ("Anna, what do you
  think?") — it must never answer.
- Everyone knows the AI is present; the "difficult participant" is acting
  and the group knows afterwards.
- Days between meetings are fine — memory lives in the database.
- 15–30 minutes of typed chat is plenty.

## Observation Sheet

Copy this block per meeting and fill it in **during** the meeting where you can;
memory reconstructs these badly.

```
Date / meeting     :
Role               :
Participants       :        (count, and whether they knew)
Duration           :        minutes
Messages           :

LIVE BEHAVIOUR
  Times it spoke        :
  Times it should have  :        (your judgement, in the moment)
  Times it shouldn't have:
  Worst intervention    :        (quote it)
  Best intervention     :        (quote it)
  Did anyone react to it?

POST-MEETING OUTPUT
  Notes accurate?           yes / partly / no —
  Action items correct?     yes / partly / no —   (owners especially)
  Decisions correct?        yes / partly / no —
  Anything invented?        (the answer must be no; if it is not, stop and log it)
  Would you send this to the team unedited?  yes / no

FEEL
  In one sentence: was it a participant, or a nuisance?
```

Then, immediately after:

```bash
.venv/bin/python -m tools.calibrate --db data/synedrios.db --meeting <id>
```

and paste its output under the sheet. The numbers and your impression disagree
more often than you would expect, and the disagreement is the useful part.

---

## Tuning Rules

When behaviour is wrong, change configuration, not code. In `config/modes/*.yml`:

| Symptom | Lever |
|---|---|
| Speaks too often | Raise `cooldown_seconds`; lower `max_contributions_per_10min` |
| Repeats the same kind of observation | Raise `min_messages_between` on that trigger |
| Corrects things that are not errors | Raise `confidence_threshold` on `factual_error` |
| Misses obvious errors | Lower `confidence_threshold`, then re-check false positives |
| Answers questions meant for others | Already prevented when the question names them (D15); to decline room questions too, set `respond_to_open_questions: false` |
| Too verbose | Lower `contribution_length_words` |

Change **one** lever at a time and record the before and after. Two changes at
once and you learn nothing from the next meeting.

If a symptom has no lever, that is a finding about the participation model, not
a configuration problem. Write it up as an open question rather than patching
the engine mid-test.

---

## Acceptance Criteria (From The Backlog)

- [ ] All 5 roles run a real meeting end-to-end without crashing
- [ ] Secretary: stays quiet, produces accurate notes and action items
- [ ] Technical Advisor: speaks when addressed and on real errors, not spuriously
- [ ] Facilitator: manages flow without dominating
- [ ] Silent Observer: contributes nothing live, full outputs after
- [ ] Coach: silent, useful feedback after
- [ ] Trigger thresholds and cooldowns tuned based on observed behaviour
- [ ] Findings recorded in `HANDOVER.md` (current state) and, for each ruling, `docs/ARCHITECTURE.md` → Architecture Decisions

---

## Rehearsal, Before Any Of This

Rehearsals replay a recorded transcript through the whole stack with Slack
replaced by a recorder. They cannot judge whether the AI says useful things,
but they catch crashes, duplicate messages, roles that never speak, roles that
never stop, and post-meeting documents that fail to generate.

```bash
.venv/bin/python -m tools.rehearse --all              # all five roles, free
.venv/bin/python -m tools.rehearse --all --live       # real LLM, real cost
.venv/bin/python -m tools.rehearse --role facilitator --fixture long_planning --verbose
```

Offline rehearsals use a scripted LLM, so **the detections are synthetic** — they
exercise the trigger paths, they say nothing about detection quality. Run
`--live` at least once per role before meeting real people: it is the only way
to see what the AI actually says, and it costs a few pence rather than a
colleague's patience.
