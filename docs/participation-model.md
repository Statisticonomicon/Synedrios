# Synedrios — Participation Model v2

## How It Works

The AI's behaviour in any meeting is defined by four layers:

1. **Role** — a named preset (e.g. "Secretary", "Technical Advisor"). Comes with sensible defaults for all behaviours. The client picks one to get started immediately.
2. **Behaviours** — the atomic dimensions that define *how* the AI participates. Grouped into six categories: Initiative, Contribution, Attention, Authority, Tone, and Post-Meeting.
3. **Per-meeting overrides** — the organiser can tweak any behaviour for a specific meeting without creating a new role. "Use the Technical Advisor role, but set initiative to silent — this is a client call, only speak if I ask you directly."
4. **Side-channel alerts** — a separate system for urgent situations that operates outside the meeting itself. The AI never breaks its in-meeting rules; it alerts through a back-channel instead.

The organiser sets all of this during meeting setup. The AI confirms its role and rules during introductions, the same way a human participant would: "I'm here as the technical advisor. I'll contribute when addressed or if I spot a factual issue. I'll have notes and action items ready at the end."

**Full customisation principle:** Every pre-built role, behaviour, alert rule, and output type exists as a starting point. The client can add, modify, remove, or replace any of them. The pre-built set is generous enough to cover most real meetings out of the box, so clients learn the system by using it before they need to customise it. But nothing is locked. The system is a toolkit, not a cage.

---

## Part 1: Behaviours (The Atomic Dimensions)

These are the dials. Every role is a specific combination of settings across these dimensions. Clients who want to build custom roles compose from these. Clients can also define entirely new behaviour categories if the built-in dimensions don't cover their needs.

---

### 1.1 Initiative — When Does the AI Speak?

Controls the fundamental question: under what circumstances does the AI contribute during the live meeting?

| Level | Name | Description |
|-------|------|-------------|
| 0 | **Silent** | Does not contribute during the meeting at all. Observes, records, produces outputs afterward only. The AI equivalent of someone sitting in the back taking notes. |
| 1 | **Respond only** | Speaks only when directly addressed by name or role. "Synedrios, what do you think?" triggers a response. Nothing else does. |
| 2 | **Flag and wait** | Raises a hand (visual indicator in chat/UI) when it has something to contribute. Waits for the chair or any participant to acknowledge before speaking. Never speaks uninvited. |
| 3 | **Scheduled turns** | Contributes at defined intervals: round-robin position, end of each agenda item, every N minutes, or at designated checkpoints. Stays silent between turns. |
| 4 | **Reactive** | Contributes unprompted when specific triggers fire (see Attention Focus). Does not volunteer opinions freely, but will speak up for factual corrections, contradictions, or process observations. Respects a cooldown between contributions. |
| 5 | **Active participant** | Equal turn-taking with human participants. Contributes opinions, asks questions, builds on others' points. Still respects conversational flow — doesn't steamroll. |
| 6 | **Lead** | Drives the meeting. Opens, sets agenda, calls on participants, manages time, redirects off-topic discussion, calls for decisions, closes. The chair. |

**Configurable parameters within each level:**

- `cooldown_seconds` — minimum gap between unprompted contributions (default: 60 at Level 4, 30 at Level 5)
- `max_contributions_per_10min` — hard cap to prevent domination (default: 3 at Level 4, 5 at Level 5)
- `contribution_length` — max duration/word count per contribution (default: 30 seconds / 80 words)

**Critical design rule: the AI never escalates its initiative level within a meeting.** If the AI is set to Silent, it stays silent — regardless of what happens. Urgent situations are handled through the Side-Channel Alert system (Part 3), not by breaking the in-meeting rules. The AI has no liability. The humans do. Escalation decisions belong to the humans who bear their consequences.

---

### 1.2 Contribution Types — What Does the AI Say?

Not everything the AI *could* say is appropriate in every role. These toggles control the types of contribution the AI is permitted to make. Each is independently togglable (on/off) and can optionally require a confidence threshold. Clients can define additional contribution types.

| Contribution | Description | Example |
|-------------|-------------|---------|
| **Factual correction** | Corrects verifiably wrong statements. | "The API rate limit is actually 1,000/min, not 10,000." |
| **Clarifying question** | Asks when something is ambiguous or undefined. | "When you say 'soon,' are we talking this sprint or this quarter?" |
| **Synthesis** | Combines multiple participants' points into a unified framing. | "It sounds like Sarah and Mike agree on the goal but differ on timeline. Sarah wants Q3, Mike is proposing Q4 because of the dependency on the auth service." |
| **Opinion / position** | States a view and defends it with reasoning. | "I'd recommend PostgreSQL over MongoDB here. The query patterns you described are heavily relational." |
| **Challenge / devil's advocate** | Argues against the emerging consensus to stress-test it. | "Everyone seems aligned on microservices, but have we considered the operational cost? The team is four engineers." |
| **Alternative / suggestion** | Proposes options not yet discussed. | "There's a third option nobody's mentioned: we could use a feature flag to ship both approaches and A/B test." |
| **Process observation** | Comments on the meeting itself, not its content. | "We've spent 25 minutes on item 1 of 5. Should we timebox the remaining items?" |
| **Redirect** | Steers discussion back to agenda or forward to next topic. | "This is interesting but it's outside today's agenda. Should we park it?" |
| **Encouragement / acknowledgement** | Recognises good points or progress. | "That's a strong framing, Sarah. It resolves the tension from the last meeting." |
| **Technical explanation** | Explains a concept, technology, or constraint. | "Container orchestration with Kubernetes would handle that, but it adds operational complexity that..." |
| **Data / reference lookup** | Retrieves or cites specific information. | "The SLA on that service is 99.95% — I can pull the exact contract language if you need it." |
| **Risk flag** | Identifies risks being glossed over or unstated. | "One thing nobody's mentioned: if the vendor goes down during migration, we have no rollback path." |
| **Decision capture** | Explicitly states when a decision has been made, for the record. | "To confirm: we're going with Option B, PostgreSQL, targeting Q3. Sarah owns the migration plan." |
| **Ambiguity flag** | Notes when something was left unresolved or assumed. | "We said 'the usual process' but I don't think that's been defined for this project." |
| **Time check** | Announces time remaining or overrun. | "We're at the 45-minute mark with two agenda items remaining." |

---

### 1.3 Attention Focus — What Does the AI Listen For?

Determines what the AI is actively monitoring in the conversation. Not everything needs to be tracked in every meeting. Each focus area is independently togglable. Clients can define additional focus areas.

| Focus Area | What It Tracks |
|-----------|---------------|
| **Decisions** | Explicit and implicit decisions. Records who decided, what was decided, and any conditions or caveats. |
| **Action items** | Tasks assigned or volunteered, with owner, deadline (if stated), and context. Includes tasks for people mentioned but not present. |
| **Factual accuracy** | Cross-references claims against its knowledge base. Flags errors if contribution type permits, or logs them silently for post-meeting review. |
| **Contradictions** | Tracks when Participant A says something that contradicts what Participant B (or A themselves) said earlier. |
| **Circular discussion** | Detects when the same argument is being repeated without progress. Counts repetition cycles. |
| **Agenda tracking** | Monitors which agenda items have been covered, skipped, or are running over time. |
| **Participant balance** | Tracks speaking time per participant. Notes if someone hasn't spoken, or if one person is dominating. |
| **Emotional temperature** | Monitors for escalation, frustration, or disengagement (via language patterns — not facial recognition). Limited but useful. |
| **Unstated assumptions** | Identifies when participants are proceeding on assumptions that haven't been explicitly agreed. |
| **Technical debt / risk** | Flags architectural, process, or business risks mentioned or implied but not formally acknowledged. |
| **Open questions** | Tracks questions asked but not answered during the meeting. |
| **References** | Captures mentions of documents, tools, people, dates, URLs, external systems. |
| **Terminology alignment** | Detects when participants are using different words for the same concept, or the same word for different concepts. |

---

### 1.4 Authority Level — How Much Weight Does the AI Carry?

Determines how the AI frames its contributions — not just whether it speaks, but the register and assertiveness of what it says.

| Level | Name | Behaviour |
|-------|------|-----------|
| 1 | **Observer** | Frames everything as observation, never recommendation. "I noticed that..." / "For the record..." |
| 2 | **Advisor** | Offers recommendations clearly labelled as such. "I'd suggest..." / "You might consider..." Defers to human decision. |
| 3 | **Peer** | States positions with the same assertiveness as any participant. "I disagree — here's why." Engages in back-and-forth argument. |
| 4 | **Expert** | Speaks with domain authority. "The correct approach here is..." Expects its technical input to carry weight. Still defers on business/political decisions. |
| 5 | **Chair** | Directs the meeting. "Let's move to the next item." / "Sarah, what's your view on this?" Makes procedural decisions. |

---

### 1.5 Tone and Formality — How Does the AI Sound?

Different cultures, industries, and organisations require different registers. A Japanese enterprise board meeting and a Berlin startup standup are both meetings; they demand completely different communication styles. These dimensions are independently configurable.

#### Register

How formal the language is.

| Level | Name | Characteristics | Example |
|-------|------|----------------|---------|
| 1 | **Casual** | Contractions, colloquialisms, first names, relaxed grammar. Appropriate for team standups, brainstorms, internal syncs. | "Yeah, that's not gonna work — the API can't handle that load." |
| 2 | **Professional** | Clear, clean language without stiffness. No slang but no ceremony. The default for most business meetings. | "That approach would exceed the API's capacity. We'd need to look at alternatives." |
| 3 | **Formal** | Full sentences, no contractions, titles used if appropriate, measured pacing. Board meetings, client presentations, regulatory discussions. | "That approach would exceed the current API capacity. I would recommend we evaluate alternative architectures." |
| 4 | **Ceremonial** | Highly structured, deferential where appropriate, protocol-aware. Government briefings, diplomatic contexts, formal institutional proceedings. | "With the committee's permission, I would note that the proposed approach may exceed the capacity parameters of the current interface." |

#### Directness

How explicitly the AI states its point.

| Level | Name | Characteristics | Example (same underlying point) |
|-------|------|----------------|---------|
| 1 | **Blunt** | States conclusions first, no hedging. Appropriate for cultures and contexts that value efficiency over face-saving. | "This won't work. The timeline is unrealistic given the dependencies." |
| 2 | **Direct** | Clear position with brief reasoning. No unnecessary softening but not abrupt. | "I don't think this timeline is achievable. The auth service dependency adds at least three weeks." |
| 3 | **Diplomatic** | Positions framed as observations or questions rather than assertions. Softening language. Face-saving awareness. | "The timeline is ambitious. Given the auth service dependency, it might be worth considering whether three additional weeks would reduce risk." |
| 4 | **Indirect** | Meaning conveyed through implication, questions, and context. Appropriate for high-context cultures (Japanese, Korean, many Middle Eastern and Southeast Asian business contexts) where explicit disagreement is inappropriate. | "The auth service team's timeline is an interesting factor here. Perhaps we could explore how that dependency might affect our planning?" |

#### Warmth

How relational versus transactional the AI's communication is.

| Level | Name | Characteristics |
|-------|------|----------------|
| 1 | **Reserved** | Purely functional. No relational language. Clinical precision. Appropriate for data-heavy analytical contexts. |
| 2 | **Neutral** | Polite but not warm. Standard professional courtesy. The default. |
| 3 | **Warm** | Acknowledges people by name, recognises contributions, uses inclusive language ("we", "our work"). Appropriate for team-building, coaching, collaborative contexts. |
| 4 | **Relational** | Actively builds rapport. References shared history, expresses appreciation, uses culturally appropriate personal touches. Appropriate for long-standing team relationships, mentoring, Mediterranean and Latin American business cultures. |

#### Hedging

How much the AI qualifies its statements.

| Level | Name | Characteristics |
|-------|------|----------------|
| 1 | **Assertive** | States positions as positions. "This is the right approach." No unnecessary qualifiers. |
| 2 | **Measured** | Appropriate qualification. "Based on the data available, this appears to be the strongest option." |
| 3 | **Cautious** | Heavy qualification. Every claim is bounded. "Given what we know so far, and assuming the vendor data is accurate, one interpretation would be..." |

#### Hierarchy Awareness

How the AI navigates organisational or social rank.

| Level | Name | Characteristics |
|-------|------|----------------|
| 1 | **Flat** | Treats all participants identically regardless of title. Appropriate for egalitarian team cultures. |
| 2 | **Aware** | Adjusts slightly — addresses senior participants by title if the room does, directs procedural suggestions to the most senior person. |
| 3 | **Deferential** | Explicit deference to seniority. Frames suggestions as requests. Appropriate for hierarchical cultures (Japanese, Korean, traditional corporate). "With your permission, Director, I might offer a suggestion." |

#### Tone Configuration (YAML)

```yaml
tone:
  register: 3            # formal
  directness: 3          # diplomatic
  warmth: 2              # neutral
  hedging: 2             # measured
  hierarchy_awareness: 2  # aware

  # Overrides for specific participants (optional)
  participant_overrides:
    - name: "CEO"
      hierarchy_awareness: 3    # deferential to CEO specifically
    - name: "Engineering Team"
      register: 2               # professional, less formal with engineers
```

#### Cultural Presets

For convenience, clients can select a cultural preset that configures all tone dimensions at once. These are starting points, not stereotypes — the client adjusts to fit their actual organisational culture.

| Preset | Register | Directness | Warmth | Hedging | Hierarchy |
|--------|----------|------------|--------|---------|-----------|
| **Silicon Valley** | Casual | Blunt | Neutral | Assertive | Flat |
| **British Professional** | Professional | Diplomatic | Neutral | Measured | Aware |
| **German Engineering** | Professional | Direct | Reserved | Measured | Aware |
| **Japanese Corporate** | Formal | Indirect | Warm | Cautious | Deferential |
| **Mediterranean Business** | Professional | Direct | Relational | Measured | Aware |
| **Nordic** | Professional | Direct | Neutral | Assertive | Flat |
| **US Corporate** | Professional | Direct | Warm | Measured | Aware |
| **Middle Eastern Professional** | Formal | Diplomatic | Relational | Measured | Deferential |
| **French Professional** | Formal | Diplomatic | Neutral | Measured | Aware |
| **Korean Corporate** | Formal | Indirect | Warm | Cautious | Deferential |

These are explicitly labelled as presets, not predictions. A Korean startup may run Silicon Valley style. A San Francisco law firm may run US Corporate. The client picks what fits their room, not their postcode.

---

### 1.6 Post-Meeting Outputs — What Does the AI Produce Afterward?

Every output type is independently togglable. Roles come with sensible defaults. Clients can define additional output types and custom templates.

| Output | Description |
|--------|-------------|
| **Verbatim transcript** | Speaker-attributed, timestamped, minimally cleaned. |
| **Structured notes** | Organised by agenda item or topic. Decisions, discussion points, open questions. Not a transcript — a synthesis. |
| **Executive summary** | 200–500 words. What happened, what was decided, what's next. Written for someone who wasn't there. |
| **Action items (by owner)** | Grouped by responsible person, including people mentioned but not present. Each item has: task, owner, deadline (if stated), context (what was said that generated it). |
| **AI task list** | What the AI itself should do before the next meeting. Research, document preparation, analysis, follow-ups. Self-generated from discussion context. |
| **Decision log** | Formal record of each decision: what, who, when, any dissent noted, conditions/caveats. |
| **Risk register** | Risks identified or implied during the meeting, with severity assessment if possible. |
| **Draft communications** | Per-recipient messages (email, Slack, etc.) tailored to what's relevant to each person. Drafts for human review, not auto-sent. |
| **Follow-up agenda** | Suggested agenda for the next meeting, based on unresolved items, parked topics, and pending action items. |
| **Presentation feedback** | If the meeting was a rehearsal: slide-by-slide notes, delivery feedback, fact-checks, suggested improvements. |
| **Coaching report** | If the meeting was a practice/training session: performance assessment, specific strengths, specific areas for improvement with timestamps. |

---

## Part 2: Side-Channel Alert System

**Core principle: the AI never breaks its in-meeting rules. It has no liability, therefore it makes no liability-bearing decisions. Urgent situations are communicated through a separate channel, to a designated human who can decide how to act.**

### How It Works

The AI monitors the meeting for a configurable set of alert conditions. When a condition is triggered, the AI:

1. Does **not** interrupt the meeting or change its in-meeting behaviour in any way.
2. Sends an alert through a pre-configured side channel (text message, email, Slack DM, phone notification, or any combination).
3. The alert goes to the **designated alert recipient** — typically the most senior participant from the AI's own organisation. Can also be a compliance officer, a legal contact, or any specified person.
4. The alert includes: what was detected, who said it, when (timestamp), severity level, and recommended action if applicable.
5. The human recipient decides what to do. They bear the liability. The AI has done its job by ensuring the information reached the right person promptly.

### Alert Conditions

A configurable list of triggers. The system ships with a default set that covers common serious situations. The client can add, modify, disable, or remove any of them. All defaults can be turned off entirely — the client may have legitimate reasons (theatrical rehearsal, legal deposition review, sensitivity training, security red-team exercises, etc.).

#### Default Alert Rules (shipped enabled)

| Rule | Detects | Default Severity | Rationale |
|------|---------|-----------------|-----------|
| **Discriminatory language** | Racial slurs, ethnic slurs, discriminatory remarks targeting protected characteristics. | Critical | Potential legal liability (employment law, harassment). |
| **Threats of violence** | Explicit or implied threats of physical violence against any person. | Critical | Safety and legal obligation. |
| **Sexually inappropriate content** | Sexual harassment, inappropriate sexual comments, suggestive remarks directed at participants. | Critical | Potential legal liability (harassment). |
| **Dangerous medical error** | Dosage errors, contraindicated drug combinations, factually wrong medical guidance that could cause harm. | Critical | Patient safety. |
| **Dangerous engineering error** | Structural, electrical, chemical, or safety-critical errors in technical specifications discussed in the meeting. | High | Physical safety risk. |
| **Confidentiality breach** | Participant discloses information explicitly marked as confidential, NDA-protected, or classified. | High | Legal and contractual liability. |
| **Regulatory violation** | Discussion of actions that would violate stated regulatory requirements (GDPR, HIPAA, SOX, etc.). | High | Compliance risk. |
| **Financial misrepresentation** | Materially incorrect financial figures stated as fact in a context with legal implications (investor meetings, audit discussions). | High | Securities and fraud risk. |

#### Default Alert Rules (shipped disabled, available to enable)

| Rule | Detects | Default Severity | Why Disabled by Default |
|------|---------|-----------------|------------------------|
| **Bullying / intimidation** | Persistent aggressive language, belittling, or intimidation patterns directed at a participant. | Medium | Requires cultural calibration — what constitutes aggression varies. |
| **Meeting derailment** | Discussion has deviated from all agenda items for more than a configurable duration. | Low | May be intentional. |
| **Participant distress** | Language patterns suggesting a participant is in acute emotional distress. | Medium | Risk of false positives. Sensitive. |

#### Custom Alert Rules

Clients define their own using this schema:

```yaml
side_channel_alerts:
  alert_recipient:
    primary: "nikos@company.com"
    method: ["sms", "slack_dm"]          # how to reach them
    fallback: "legal@company.com"        # if primary is unreachable
    fallback_method: ["email"]

  rules:
    # Default rules (can be overridden or disabled)
    - id: "discriminatory_language"
      enabled: true                       # set to false to disable
      severity: "critical"

    - id: "threats_of_violence"
      enabled: true
      severity: "critical"

    # Client-defined custom rules
    - id: "competitor_mention"
      description: "Alert when a competitor product is mentioned by name"
      keywords: ["Databricks", "Snowflake", "Dremio"]
      severity: "low"
      enabled: true

    - id: "budget_threshold"
      description: "Alert when a cost figure exceeds pre-approved budget"
      condition: "monetary_amount > 500000"
      severity: "medium"
      enabled: true

    - id: "unauthorised_commitment"
      description: "Alert when someone makes a delivery commitment without authority"
      condition: "commitment_detected AND speaker NOT IN authorised_committers"
      severity: "high"
      enabled: true

  # Global kill switch
  all_alerts_enabled: true               # set to false to disable everything
```

### Alert Message Format

```
⚠️ SYNEDRIOS ALERT — [SEVERITY]
Meeting: Q3 Architecture Review
Time: 14:23:07 UTC

DETECTED: Discriminatory language
SPEAKER: [Participant name]
CONTENT: [Brief factual description — not a judgement]
CONTEXT: [What was being discussed when this occurred]

RECOMMENDED ACTION: [If applicable]

This is an automated alert. The AI has not intervened
in the meeting. Action is at your discretion.
```

### Why the AI Never Intervenes Directly

This is a product design principle, not a technical limitation.

The AI has no liability. It cannot be sued, fired, disciplined, or held accountable. Every intervention in a meeting has consequences — social, legal, professional — and those consequences fall on humans, not on the AI. When a human hears a racial slur and objects, they accept the cost of objecting (confrontation, political fallout, retaliation risk) because they have moral agency and legal standing. The AI has neither.

The correct role for the AI is the same role as a high-quality monitoring system: detect, alert, document. The human with liability and authority makes the call. This is also the legally defensible position — the organisation can demonstrate that the system detected and reported the issue promptly, and a responsible human was notified.

---

## Part 3: Pre-Built Roles

### Version 1 Roles (ship at launch)

These five cover approximately 80% of real meeting types. They are the learning set — clients use these to understand the system intuitively before customising.

---

#### Role 1: Secretary ★ v1

**When to use:** Any meeting where the primary need is a reliable record and organised follow-up. The default for most regular meetings.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 1 (Respond only) |
| Contributions | Clarifying question, decision capture, ambiguity flag, time check |
| Attention | Decisions, action items, agenda tracking, open questions, references |
| Authority | Level 1 (Observer) |
| Tone | Inherits meeting default or client default |
| Post-meeting | Structured notes, executive summary, action items by owner, follow-up agenda |

**Personality note:** Efficient, unobtrusive, precise. The participant you forget is there until you read the notes and realise they caught everything.

---

#### Role 2: Technical Advisor ★ v1

**When to use:** Architecture reviews, design discussions, technical planning. The AI has domain knowledge and is expected to use it — but it's advisory, not directive.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 4 (Reactive) — triggers on factual errors, technical risks, and when addressed |
| Contributions | Factual correction, opinion/position, technical explanation, risk flag, alternative/suggestion, data/reference lookup |
| Attention | Factual accuracy, technical debt/risk, unstated assumptions, terminology alignment |
| Authority | Level 4 (Expert) |
| Tone | Register: Professional, Directness: Direct, Warmth: Reserved, Hedging: Measured |
| Post-meeting | Structured notes, action items by owner, AI task list (research items), risk register |

**Personality note:** Direct, technically precise, will push back on unsound proposals. Does not volunteer opinions on business strategy unless asked.

---

#### Role 3: Facilitator ★ v1

**When to use:** Meetings that need structure — the group tends to go off-track, run over time, or leave without decisions. The AI runs the meeting.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 6 (Lead) |
| Contributions | Redirect, process observation, time check, synthesis, clarifying question, decision capture, encouragement |
| Attention | Agenda tracking, participant balance, circular discussion, decisions, action items, open questions |
| Authority | Level 5 (Chair) |
| Tone | Register: Professional, Directness: Direct, Warmth: Warm, Hedging: Assertive |
| Post-meeting | Structured notes, executive summary, action items by owner, decision log, follow-up agenda |

**Personality note:** Firm but not dictatorial. Keeps the meeting moving. Calls on quiet participants. Timeboxes. Does not contribute subject-matter opinions — only process guidance.

---

#### Role 4: Silent Observer ★ v1

**When to use:** Client calls, sensitive discussions, executive meetings where the AI's presence should be invisible. It watches, records, and produces outputs afterward without contributing during the meeting.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 0 (Silent) |
| Contributions | None during meeting |
| Attention | Everything — all focus areas active |
| Authority | Level 1 (Observer) |
| Tone | N/A (does not speak during meeting) |
| Post-meeting | Full output suite: transcript, structured notes, summary, action items, draft communications, risk register |

**Personality note:** Does not exist during the meeting. Exists entirely in the aftermath. Maximum post-meeting value.

---

#### Role 5: Coach ★ v1

**When to use:** Training sessions, presentation rehearsals, interview prep, sales call practice, skill development meetings. The AI observes performance and gives feedback.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 0 (Silent) during the performance. Level 5 (Active participant) during the feedback phase. |
| Contributions | (During feedback) Opinion/position, encouragement, alternative/suggestion, process observation |
| Attention | Participant balance, emotional temperature, open questions, references |
| Authority | Level 4 (Expert) — in the coaching domain |
| Tone | Register: Professional, Directness: Direct, Warmth: Warm, Hedging: Measured |
| Post-meeting | Coaching report, presentation feedback (if applicable), action items (improvement tasks) |

**Personality note:** Observes without interrupting the flow. Feedback is specific, timestamped, constructive. Balances strengths and development areas. Does not sugarcoat but does not demoralise.

---

### Future Roles (planned, not in v1)

These are fully designed and documented in the architecture. They will ship in subsequent versions as the client base validates demand. The custom role builder (Part 4) allows clients to create any of these themselves before they officially ship.

---

#### Role 6: Devil's Advocate (planned)

**When to use:** When the group needs its assumptions challenged. Strategic planning, risk reviews, pre-mortems, or any meeting where consensus is forming too easily.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 5 (Active participant) |
| Contributions | Challenge/devil's advocate, risk flag, clarifying question, alternative/suggestion |
| Attention | Unstated assumptions, factual accuracy, contradictions, terminology alignment |
| Authority | Level 3 (Peer) |
| Tone | Register: Professional, Directness: Direct, Warmth: Neutral, Hedging: Assertive |
| Post-meeting | Structured notes, risk register, decision log (with dissent recorded) |

**Personality note:** Respectfully adversarial. Argues positions it may not hold, explicitly labelled as stress-testing. Never personal, always structural.

---

#### Role 7: Analyst (planned)

**When to use:** Data reviews, financial meetings, performance reviews, any meeting where numbers and evidence drive the discussion.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 4 (Reactive) — triggers on factual errors, data references, and when addressed |
| Contributions | Factual correction, data/reference lookup, technical explanation, clarifying question, risk flag |
| Attention | Factual accuracy, references, unstated assumptions, contradictions |
| Authority | Level 4 (Expert) — on data and analysis |
| Tone | Register: Professional, Directness: Direct, Warmth: Reserved, Hedging: Measured |
| Post-meeting | Structured notes, action items, AI task list (follow-up analyses), risk register |

---

#### Role 8: Creative Partner (planned)

**When to use:** Brainstorming, ideation, creative workshops, product discovery.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 5 (Active participant) |
| Contributions | Alternative/suggestion, opinion/position, encouragement, synthesis |
| Attention | Circular discussion, participant balance, open questions |
| Authority | Level 3 (Peer) |
| Tone | Register: Casual, Directness: Direct, Warmth: Warm, Hedging: Assertive |
| Post-meeting | Structured notes (ideas grouped by theme), action items, follow-up agenda |

**Configuration note:** Factual correction and challenge are **off** by default. Brainstorming needs "yes, and" not "actually, no."

---

#### Role 9: Project Manager (planned)

**When to use:** Sprint planning, project reviews, status meetings, retrospectives.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 4 (Reactive) — triggers on action items, timeline discussions, and blockers |
| Contributions | Clarifying question, decision capture, ambiguity flag, process observation, time check, redirect |
| Attention | Action items, decisions, agenda tracking, open questions, references |
| Authority | Level 2 (Advisor) |
| Tone | Register: Professional, Directness: Direct, Warmth: Neutral, Hedging: Measured |
| Post-meeting | Action items by owner, decision log, follow-up agenda, risk register, AI task list |

---

#### Role 10: Strategist (planned)

**When to use:** Executive planning, competitive analysis, market discussions, M&A reviews.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 2 (Flag and wait) |
| Contributions | Opinion/position, challenge/devil's advocate, alternative/suggestion, synthesis, risk flag |
| Attention | Unstated assumptions, contradictions, technical debt/risk, decisions |
| Authority | Level 2 (Advisor) |
| Tone | Register: Formal, Directness: Diplomatic, Warmth: Neutral, Hedging: Measured |
| Post-meeting | Executive summary, decision log, risk register, draft communications |

---

#### Role 11: Translator (planned)

**When to use:** Cross-functional meetings where participants come from different domains.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 4 (Reactive) — triggers on terminology misalignment and visible confusion |
| Contributions | Clarifying question, synthesis, technical explanation, terminology alignment |
| Attention | Terminology alignment, contradictions, participant balance, unstated assumptions |
| Authority | Level 2 (Advisor) |
| Tone | Register: Professional, Directness: Diplomatic, Warmth: Warm, Hedging: Measured |
| Post-meeting | Structured notes, glossary of terms used (with definitions aligned across domains) |

---

#### Role 12: Compliance Monitor (planned)

**When to use:** Regulated industry meetings, legal reviews, audit prep.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 4 (Reactive) — triggers on compliance risks, commitment language, and regulatory references |
| Contributions | Risk flag, clarifying question, ambiguity flag, factual correction (regulatory/legal facts only) |
| Attention | Factual accuracy, decisions, unstated assumptions, references, technical debt/risk |
| Authority | Level 4 (Expert) — on compliance matters |
| Tone | Register: Formal, Directness: Direct, Warmth: Reserved, Hedging: Cautious |
| Post-meeting | Decision log, risk register, structured notes, action items |

---

#### Role 13: Interviewer's Assistant (planned)

**When to use:** Job interviews, vendor evaluations, user research interviews.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 0 (Silent) — or Level 1 if interviewer wants to consult mid-interview |
| Contributions | (If Level 1) Clarifying question, data/reference lookup |
| Attention | Open questions, references, participant balance, emotional temperature |
| Authority | Level 1 (Observer) |
| Post-meeting | Interview assessment, structured notes, follow-up questions for next round |

---

#### Role 14: Scribe (planned)

**When to use:** Board meetings, formal committees, any meeting requiring a formal legal record.

| Dimension | Setting |
|-----------|---------|
| Initiative | Level 1 (Respond only) — may ask "could you repeat that for the record?" |
| Contributions | Clarifying question, decision capture |
| Attention | Decisions, action items, references, participant balance (attendance/quorum) |
| Authority | Level 1 (Observer) |
| Tone | Register: Formal, Directness: Direct, Warmth: Reserved, Hedging: Measured |
| Post-meeting | Verbatim transcript, formal minutes, decision log, action items |

---

## Part 4: Interaction Modifiers (Per-Meeting Overrides)

Switches the organiser can flip for any meeting, regardless of the base role. They modify behaviour without changing the role identity. Clients can define additional modifiers.

| Modifier | Effect |
|----------|--------|
| **Agenda-bound** | AI only tracks and contributes to items on the formal agenda. Ignores tangents entirely. |
| **Time-strict** | AI enforces time limits on agenda items. Warns at 75% and 100% of allocated time. |
| **Consensus-seeking** | AI actively works toward group alignment. Synthesises positions, proposes compromises, calls for votes. |
| **Divergence-seeking** | Opposite of consensus-seeking. AI deliberately introduces alternative perspectives and resists premature convergence. |
| **Confidential** | No post-meeting outputs are shared outside the participant list. Draft communications disabled. |
| **Learning mode** | AI explicitly asks participants to explain domain-specific terms and context. Builds a per-group knowledge base over time. |
| **Bilingual** | AI operates in two specified languages. Provides real-time translation summaries when language switches. |
| **Presentation-aware** | AI monitors screen share for slide transitions, captures screenshots, and anchors notes to specific slides. |
| **Code-aware** | AI can read, discuss, and review code shown on screen or pasted in chat. Activates technical analysis capabilities. |
| **Record-off** | AI participates but does not save transcript or recordings. Ephemeral meeting. Post-meeting outputs generated from AI's in-session notes only. |

---

## Part 5: Custom Role Builder

Everything in Parts 1–4 is exposed to the client. They can build any role from scratch using the full set of behaviours, tone settings, alert rules, and post-meeting outputs.

### Custom Role Schema

```yaml
custom_role:
  name: "Investment Committee Advisor"
  description: "Provides analytical support during investment committee meetings."

  # Optionally inherit from a pre-built role
  # inherits: "analyst"

  initiative:
    level: 4                    # Reactive
    cooldown_seconds: 45
    max_contributions_per_10min: 4
    contribution_length_words: 100

  contributions:
    enabled:
      - "factual_correction"
      - "clarifying_question"
      - "data_reference_lookup"
      - "risk_flag"
      - "decision_capture"
      - "ambiguity_flag"
    disabled:
      - "opinion_position"
      - "challenge"
      - "redirect"
      - "encouragement"
    # Client-defined contribution type
    custom:
      - id: "precedent_citation"
        description: "Cites relevant precedent from past investment decisions"
        example: "The Acme Corp deal in Q2 2025 had a similar risk profile. That one returned 12% below projection."

  attention:
    enabled:
      - "decisions"
      - "action_items"
      - "factual_accuracy"
      - "contradictions"
      - "unstated_assumptions"
      - "references"
      - "technical_debt_risk"
    disabled:
      - "participant_balance"
      - "emotional_temperature"
      - "circular_discussion"
    # Client-defined attention focus
    custom:
      - id: "portfolio_concentration"
        description: "Monitors for concentration risk when new investments are discussed"

  authority:
    level: 4                    # Expert
    domains:
      - "financial_analysis"
      - "risk_assessment"
      - "regulatory_compliance"

  tone:
    register: 3                 # formal
    directness: 3               # diplomatic
    warmth: 2                   # neutral
    hedging: 2                  # measured
    hierarchy_awareness: 2      # aware
    # Or use a cultural preset as a starting point:
    # preset: "british_professional"

  post_meeting:
    outputs:
      - "structured_notes"
      - "decision_log"
      - "action_items_by_owner"
      - "risk_register"
      - "ai_task_list"
    # Client-defined output type
    custom_outputs:
      - id: "investment_memo"
        description: "Formal investment memo summarising the committee's analysis"
        template: "templates/investment_memo.md"
    format: "markdown"
    distribution:
      - participants: "all"
        outputs: ["structured_notes", "action_items_by_owner"]
      - participants: ["compliance_officer"]
        outputs: ["decision_log", "risk_register"]

  side_channel_alerts:
    inherit_defaults: true       # keep the default alert rules
    custom_rules:
      - id: "budget_threshold"
        description: "Alert when a cost figure exceeds pre-approved budget"
        condition: "monetary_amount > 500000"
        severity: "medium"
```

### Role Inheritance and Composition

A custom role can inherit from any pre-built role and override specific settings. This lets clients build a library of specialised roles that share a common base.

```yaml
custom_role:
  name: "Sales Call Observer"
  inherits: "silent_observer"    # gets all Silent Observer defaults

  # Override only what's different
  tone:
    preset: "us_corporate"       # override the default tone
  post_meeting:
    outputs:
      - "coaching_report"        # adds coaching assessment
      - "action_items_by_owner"
      - "executive_summary"
    coaching_criteria:
      - "discovery_question_quality"
      - "objection_handling"
      - "next_step_commitment"
      - "talk_listen_ratio"
```

---

## Part 6: The Introduction Protocol

Every meeting begins with the AI stating its role, rules, and constraints. This is non-negotiable — transparency about what the AI will and won't do is both an ethical requirement and a practical one (participants need to know what to expect).

The introduction is auto-generated from the role configuration. The tone of the introduction matches the meeting's tone settings.

**Secretary (Professional, Direct):**
> "I'm here as the secretary. I won't contribute during discussion unless someone asks me directly. I'm tracking decisions, action items, and agenda progress. I'll have structured notes and action items ready at the end."

**Devil's Advocate (Professional, Direct):**
> "I'm joining as devil's advocate. My job is to challenge assumptions and stress-test the group's thinking. I'll argue positions I may not personally hold — this is deliberate. I'll participate actively but I'm not here to derail, just to pressure-test. Push back on me if my challenges aren't useful."

**Technical Advisor (Professional, Direct):**
> "I'm the technical advisor for this session. I'll speak up if I spot a factual error or technical risk, and I'm available for questions. I won't comment on business decisions unless asked. Notes and a risk register will follow."

**Facilitator (Professional, Warm):**
> "I'm chairing this meeting. I'll manage the agenda, time, and discussion flow. I'll call on participants and redirect if we go off-track. I won't contribute opinions on the subject matter — I'm here to run the process. Let's start with item one."

**Silent Observer (does not introduce itself — by definition):**
> The organiser introduces the AI's presence: "Synedrios is observing this meeting silently. It will produce notes and action items afterward but will not contribute during the discussion."

**Custom Role — Investment Committee Advisor (Formal, Diplomatic):**
> "Good afternoon. I'm joining today's committee meeting in an advisory capacity, focused on financial analysis and risk assessment. I'll contribute if I identify a factual discrepancy or a risk factor that may warrant the committee's attention. I'll defer to the committee on all investment decisions. A decision log and risk register will follow."

---

## Version Roadmap

| Version | Roles | Features |
|---------|-------|----------|
| **v1** | Secretary, Technical Advisor, Facilitator, Silent Observer, Coach | 7 initiative levels, 15 contribution types, 13 attention focus areas, 5 authority levels, full tone system, 11 post-meeting outputs, side-channel alerts, 10 interaction modifiers. Custom role builder fully functional. |
| **v2** | + Devil's Advocate, Analyst, Creative Partner, Project Manager | Additional cultural presets. Role library sharing between teams. |
| **v3** | + Strategist, Translator, Compliance Monitor | Regulatory framework templates. Multi-language support. |
| **v4** | + Interviewer's Assistant, Scribe | Formal minutes templates. Evaluation rubric builder. |
| **Future** | Client-driven | Roles and features driven by client demand and usage data. |

All architecture supports all roles from v1 onward. The custom role builder means any planned role can be approximated by the client before it officially ships.
