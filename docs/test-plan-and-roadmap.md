# Synedrios — Test Plan & Version Roadmap

## Part 1: Test Strategy

### Testing Principles

1. **The participation model is the product.** Testing must prioritise trigger accuracy, turn management correctness, and output quality over infrastructure plumbing.
2. **Reproducible meeting fixtures.** Every test uses a pre-recorded meeting transcript (a sequence of `MeetingEvent` objects) so tests are deterministic and don't require live Slack connections or LLM API calls.
3. **LLM calls are mocked in unit tests.** The adapter interface makes this trivial — swap the real adapter for a mock that returns canned responses.
4. **Integration tests hit real APIs.** Marked separately so they can be skipped in CI (they cost money and take time) but run before any release.
5. **Audit log is tested as a first-class feature.** If the audit log is wrong, the product is legally compromised. Every test that exercises the participation engine also verifies the audit trail.

### Test Levels

| Level | What It Tests | LLM Calls | Slack Connection | Speed |
|-------|--------------|-----------|-----------------|-------|
| Unit | Individual functions and classes | Mocked | No | Fast (< 1s per test) |
| Component | Full modules (engine, connector, database) | Mocked | No | Fast (< 5s per test) |
| Integration | End-to-end with real APIs | Real | No (simulated events) | Slow (10-30s per test) |
| System | Full Docker deployment with Slack | Real | Real | Manual / slow |
| Acceptance | User scenarios against stated requirements | Real | Real | Manual |

---

## Part 2: Phase 1 Test Plan

### 2.1 Unit Tests — Models (`tests/test_models.py`)

| ID | Test | Validates |
|----|------|-----------|
| M01 | Create `MeetingEvent` with all fields | Model instantiation, defaults, UUID generation |
| M02 | Create `AuditEntry`, compute hash | Hash computation is deterministic |
| M03 | `AuditEntry.finalise()` chains hashes correctly | previous_hash links to prior entry's entry_hash |
| M04 | Chain integrity: tamper with payload, hash changes | Tampering is detectable |
| M05 | `Participant` defaults are sensible | Default organisation, role, tone values |
| M06 | `TriggerResult` sorting by priority | Lower priority number sorts first |
| M07 | All enums have expected values | No typos in enum definitions |

### 2.2 Unit Tests — Database (`tests/test_database.py`)

| ID | Test | Validates |
|----|------|-----------|
| D01 | Schema creation on fresh database | All tables created, no errors |
| D02 | Create and retrieve participant | Round-trip data integrity |
| D03 | `get_default_participant` creates on first call | Auto-creation of default participant |
| D04 | `get_default_participant` returns same on second call | Idempotent |
| D05 | Create and retrieve meeting | All fields preserved |
| D06 | `update_meeting_status` changes status and ended_at | Status transitions work |
| D07 | `get_active_meeting_by_channel` finds active, ignores ended | Channel-based lookup |
| D08 | `save_event` and `get_transcript` ordered by timestamp | Chronological ordering |
| D09 | `get_recent_transcript` returns last N events in order | Reverse-then-reorder logic |
| D10 | `append_audit` creates valid hash chain across 10 entries | Chain integrity at scale |
| D11 | `append_audit` with different meeting_ids maintains separate chains | Per-meeting chain isolation |
| D12 | `save_action_item` and `get_action_items` round-trip | Action item persistence |
| D13 | `save_memory` and `get_memories` ordered by recency | Memory retrieval |
| D14 | Organisation_id filtering (prepare for multi-tenancy) | Queries respect org boundary |
| D15 | Concurrent writes don't corrupt (WAL mode) | SQLite WAL journaling works |

### 2.3 Unit Tests — Mode Configuration (`tests/test_modes.py`)

| ID | Test | Validates |
|----|------|-----------|
| C01 | Load each of the 5 YAML configs successfully | All configs parse without error |
| C02 | Secretary: initiative=1, authority=1 | Correct defaults |
| C03 | Technical Advisor: initiative=4, has factual_error trigger | Reactive triggers configured |
| C04 | Facilitator: initiative=6, authority=5 | Lead/chair mode |
| C05 | Silent Observer: initiative=0, no triggers | Fully silent |
| C06 | Coach: initiative=0, authority=4 | Silent but expert |
| C07 | `generate_introduction` produces non-empty text for all modes | Introduction generation |
| C08 | Custom introduction template uses `{name}` substitution | Template system |
| C09 | Load invalid YAML: missing required fields | Graceful error, not crash |
| C10 | Load empty directory: returns empty dict | No modes = no crash |
| C11 | Tone config defaults are correct | All tone dimensions default to 2 |
| C12 | `load_all_modes` loads exactly 5 modes from config/modes/ | Discovery works |

### 2.4 Unit Tests — LLM Adapter (`tests/test_llm.py`)

| ID | Test | Validates |
|----|------|-----------|
| L01 | `create_adapter` with provider=anthropic returns `AnthropicAdapter` | Factory pattern |
| L02 | `create_adapter` with provider=openai returns `OpenAIAdapter` | Factory pattern |
| L03 | `create_adapter` with provider=ollama sets correct base_url | Local LLM defaults |
| L04 | `create_adapter` with provider=vllm sets correct base_url | Local LLM defaults |
| L05 | `create_adapter` with unknown provider raises ValueError | Error handling |
| L06 | `AnthropicAdapter.complete` sends correct headers and body (mocked) | API contract |
| L07 | `AnthropicAdapter.complete` extracts text from content blocks | Response parsing |
| L08 | `AnthropicAdapter.complete` handles HTTP error gracefully | Error propagation |
| L09 | `OpenAIAdapter.complete` sends system message correctly | System prompt handling |
| L10 | `context_window` property returns expected values | Context budget |
| L11 | Base URL override works for VPC endpoints | Air-gapped deployment |

### 2.5 Unit Tests — Participation Engine (`tests/test_participation.py`)

This is the most critical test suite. The participation model is the product.

| ID | Test | Validates |
|----|------|-----------|
| **Trigger Evaluation** |||
| P01 | Direct address with participant name triggers `addressed_directly` | Name detection |
| P02 | Direct address with @mention triggers `addressed_directly` | @mention detection |
| P03 | Message without name does NOT trigger at initiative level 1 | Respond-only respects rules |
| P04 | Silent mode (level 0) returns empty triggers for any input | Silent means silent |
| P05 | Circular discussion detector fires after 3 repetitions | Repetition counting |
| P06 | Circular discussion does NOT fire under threshold | No false positives |
| P07 | Synthesis opportunity fires when 3+ speakers active | Multi-speaker detection |
| P08 | Synthesis opportunity does NOT fire with < 15 events | Minimum data requirement |
| **Turn Management** |||
| P09 | `can_contribute` returns True when no recent contributions | Fresh state |
| P10 | `can_contribute` returns False during cooldown period | Cooldown enforcement |
| P11 | `can_contribute` returns False when rate limit reached | Rate limiting |
| P12 | `record_contribution` updates last contribution time | State tracking |
| P13 | Rate limit window slides correctly (old entries expire) | Window-based counting |
| P14 | After cooldown expires, `can_contribute` returns True again | Recovery |
| **System Prompt Construction** |||
| P15 | System prompt includes role name | Role identity |
| P16 | System prompt includes enabled contributions | Contribution constraints |
| P17 | System prompt includes disabled contributions | Negative constraints |
| P18 | System prompt includes tone description | Tone configuration |
| P19 | System prompt includes authority level | Authority framing |
| P20 | System prompt includes word limit | Length constraint |
| P21 | System prompt includes memories when provided | Memory integration |
| **Message Construction** |||
| P22 | Messages include recent transcript context | Context window |
| P23 | Messages include trigger information | Trigger context |
| P24 | Messages include latest event content | Current event |
| **Post-Meeting Generation** |||
| P25 | `generate_post_meeting` calls LLM for each output type | Completeness |
| P26 | Secretary mode generates: notes, summary, actions, follow-up | Role-specific outputs |
| P27 | Silent observer generates full output suite | Comprehensive outputs |
| P28 | Output prompts include meeting title | Context |
| P29 | Output prompts include formatted transcript | Data completeness |
| **Cross-Role Behaviour** |||
| P30 | Secretary: only responds when addressed | Initiative level 1 |
| P31 | Technical advisor: responds to factual triggers | Initiative level 4 |
| P32 | Facilitator: high rate limit, short cooldown | Lead behaviour |
| P33 | Silent observer: never triggers, ever | Zero initiative |
| P34 | Coach: silent during meeting, same as observer | Silent performance mode |

### 2.6 Unit Tests — Slack Connector (`tests/test_slack.py`)

| ID | Test | Validates |
|----|------|-----------|
| S01 | `start meeting` command parses title from quotes | Command parsing |
| S02 | `start meeting` command parses role name | Role selection |
| S03 | `start meeting` with unknown role returns error message | Invalid role handling |
| S04 | `start meeting` in channel with active meeting returns error | Duplicate prevention |
| S05 | `end meeting` with no active meeting returns error | State validation |
| S06 | `end meeting` triggers post-meeting generation | Output pipeline |
| S07 | `status` command returns meeting info | Status reporting |
| S08 | `help` command lists available roles | Help text |
| S09 | Regular message during active meeting creates transcript event | Event capture |
| S10 | Regular message when no meeting active is ignored | No-op when inactive |
| S11 | Bot messages are ignored (no self-response loop) | Loop prevention |
| S12 | Message edits and subtypes are ignored | Clean input |
| S13 | @mention during meeting is treated as direct address | Address detection |
| S14 | Audit entry created for every transcript segment | Audit completeness |
| S15 | Audit entry created for every contribution | Audit completeness |
| S16 | Audit entry created for suppressed triggers | Audit completeness |
| S17 | Long AI responses are chunked at 3800 chars for Slack | Platform limits |

### 2.7 Integration Tests (`tests/test_integration.py`)

These hit real APIs. Mark with `@pytest.mark.integration` and skip in CI by default.

| ID | Test | Validates |
|----|------|-----------|
| I01 | Full meeting simulation: start → 10 messages → end | End-to-end flow |
| I02 | Secretary role produces coherent notes from fixture transcript | LLM output quality |
| I03 | Technical advisor responds to direct address with technical content | Role-appropriate response |
| I04 | Silent observer produces outputs without any in-meeting contributions | Silent behaviour + output |
| I05 | Action items extraction identifies owners and tasks | Post-meeting accuracy |
| I06 | Audit log chain is valid after full meeting | Chain integrity |
| I07 | Meeting with 50+ messages stays within context limits | Context management |
| I08 | Anthropic adapter handles rate limiting (429) gracefully | API resilience |
| I09 | OpenAI adapter works with same test fixture | Provider agnosticism |

### 2.8 Test Fixtures (`tests/fixtures/`)

Pre-built meeting transcripts for deterministic testing:

| Fixture | Content | Tests It Supports |
|---------|---------|-------------------|
| `simple_standup.json` | 5 participants, 15 messages, 10 minutes. Standard standup format. | Basic flow, action items |
| `technical_debate.json` | 3 participants, 40 messages, heated architecture discussion with factual errors. | Trigger evaluation, contradiction detection |
| `circular_meeting.json` | 4 participants, 30 messages, same argument repeated 4 times. | Circular discussion detection |
| `quick_decision.json` | 3 participants, 8 messages, single decision made quickly. | Decision capture, minimal meeting |
| `long_planning.json` | 6 participants, 100 messages, 60-minute planning session. | Context window management, synthesis |
| `direct_address.json` | Messages with various ways of addressing the AI: by name, @mention, indirect question. | Address detection accuracy |
| `empty_meeting.json` | Start → immediate end, no messages. | Edge case: empty transcript |

### 2.9 Test Execution

```bash
# Run all unit tests
pytest tests/ -v --ignore=tests/test_integration.py

# Run integration tests (requires API keys)
pytest tests/test_integration.py -v --run-integration

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run security audit
pip-audit --strict
```

Target coverage: 90%+ on `src/engine/`, 80%+ on everything else.

---

## Part 3: Subsequent Version Test Plans

### v1.1 — Web GUI (see Part 4 for GUI specification)

| Area | Tests |
|------|-------|
| Role builder UI | Create role → verify YAML saved correctly. Edit role → verify changes persist. Delete role → verify removal. Drag tone sliders → verify values update. |
| Meeting setup wizard | Select role → start meeting → verify Slack receives start command. Apply modifiers → verify config includes them. |
| Live monitoring | Simulate meeting events → verify dashboard updates in real-time via WebSocket. Manual override (force speak / silence) → verify engine state changes. |
| Post-meeting review | End meeting → verify outputs display correctly. Edit action items → verify changes saved. |
| Audit browser | Load meeting audit trail → verify chain integrity indicator. Filter by event type → verify correct filtering. Export → verify file format. |
| Authentication | Login/logout flow. Session expiry. Password reset. |
| Responsive design | All pages render correctly at 1920px, 1366px, 768px, 375px. |

### v2 — Voice Meetings (Recall.ai)

| Area | Tests |
|------|-------|
| Recall.ai connector | Bot joins Zoom/Teams/Meet call → receives audio stream. Speaker diarisation → correct speaker attribution. Bot leaves when meeting ends. |
| Real-time STT | Audio chunks → Deepgram API → transcript events. Latency < 2 seconds end-to-end. Speaker identification matches Recall.ai diarisation. |
| Screenshot capture | Screen share detected → screenshot captured at transitions. OCR on screenshot produces readable text. Screenshots stored and linked to transcript timestamps. |
| Multi-LLM | Same meeting fixture tested against Claude, GPT-4o, Ollama (llama3). Outputs are role-appropriate regardless of backend. Context management adapts to different context windows. |
| RBAC | Admin can create/delete roles. Manager can create avatars within their scope. User cannot create avatars above their level. Recruiter cannot access sales team's avatar memory. |
| PostgreSQL migration | Migrate from SQLite → PostgreSQL. All data preserved. All queries return same results. Concurrent writes from 5 workers don't conflict. |
| Side-channel alerts | Simulated racial slur → alert sent to designated recipient (mock SMTP/Slack). Alert includes correct severity, timestamp, speaker. Alert does NOT cause in-meeting response. Custom alert rules fire correctly. Disabled rules do NOT fire. |

### v3 — Full Voice Participant

| Area | Tests |
|------|-------|
| TTS | AI response → ElevenLabs/Piper API → audio output. Audio plays clearly. Latency < 3 seconds from trigger to speech start. |
| Turn-taking | AI waits for speaker to finish (silence detection). AI does not interrupt mid-sentence. AI respects hand-raise protocol in flag-and-wait mode. |
| Semantic memory (ChromaDB) | Store 100 meeting summaries → query "what did Sarah say about databases" → relevant results. Cross-meeting memory recall during live meeting. Memory access control: avatar A cannot access avatar B's memories. |
| Scale (50 concurrent) | 50 simulated meetings running on one worker. All trigger evaluations complete within 500ms. No meeting starves for processing time. Memory usage stays under 512 MB. |

### v4 — Cloud Marketplace

| Area | Tests |
|------|-------|
| AWS deployment | CloudFormation template deploys successfully. ECS task starts and passes health check. Metering API reports usage correctly. |
| Azure deployment | ARM template deploys successfully. Container App starts and passes health check. |
| GCP deployment | Cloud Run deployment succeeds. GKE Helm chart installs cleanly. |
| Air-gapped | Container runs with no internet access + internal LLM endpoint. All features work. No outbound connections attempted (verified with network monitor). |
| Container security | Trivy scan: zero critical/high vulnerabilities. Runs as non-root. No hardcoded secrets. No unnecessary capabilities. |

---

## Part 4: GUI Specification

The web GUI is a management and monitoring interface. The AI participates through the meeting platform (Slack, Zoom, etc.), not through the GUI. The GUI is for setup, configuration, monitoring, and review.

### When to Build

**v1.1** — immediately after Phase 1 is tested with real meetings. The YAML-and-Slack-commands approach works for you; it doesn't work for customers. The GUI is the difference between a developer tool and a product.

### Technology

FastAPI backend (already in dependencies) + HTMX frontend. No React, no build step, no JavaScript framework. HTMX gives interactive behaviour with server-rendered HTML — simpler to build, simpler to maintain, works without a separate frontend deployment. Single container stays single container.

### GUI Pages

#### 4.1 Dashboard (home page)

What the user sees when they log in.

```
┌─────────────────────────────────────────────────────────┐
│  SYNEDRIOS                                    [Settings]│
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ACTIVE MEETINGS (2)                                    │
│  ┌────────────────────────────────────────────────────┐ │
│  │ Q3 Planning  │ #engineering │ Technical Advisor     │ │
│  │ 34 min       │ 47 messages  │ 3 contributions      │ │
│  │                                        [Monitor]   │ │
│  ├────────────────────────────────────────────────────┤ │
│  │ Sales Sync   │ #sales       │ Silent Observer       │ │
│  │ 12 min       │ 18 messages  │ 0 contributions      │ │
│  │                                        [Monitor]   │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  RECENT MEETINGS                                        │
│  ┌────────────────────────────────────────────────────┐ │
│  │ Board Review  │ Yesterday  │ Secretary │ [Review]  │ │
│  │ Sprint Retro  │ 2 days ago │ Facilitator │ [Review]│ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  AI TASK QUEUE (4 pending)                    [View]    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### 4.2 Role Manager

CRUD interface for meeting roles. This replaces manual YAML editing.

```
┌─────────────────────────────────────────────────────────┐
│  ROLES                                    [+ New Role]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Secretary ★ built-in                    [Edit]   │   │
│  │ Responds when addressed. Notes + actions.         │   │
│  ├──────────────────────────────────────────────────┤   │
│  │ Technical Advisor ★ built-in            [Edit]   │   │
│  │ Expert input on demand. Risks + research.         │   │
│  ├──────────────────────────────────────────────────┤   │
│  │ Investment Committee  (custom)    [Edit] [Delete]│   │
│  │ Inherits: Analyst. Financial focus.               │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Role Editor** (clicking Edit or + New Role):

```
┌─────────────────────────────────────────────────────────┐
│  EDIT ROLE: Technical Advisor                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Name: [Technical Advisor          ]                    │
│  Description: [Domain expert for architecture...]       │
│  Inherits from: [None ▼]                                │
│                                                         │
│  ── INITIATIVE ──────────────────────────────────────   │
│  Level: ○Silent ○Respond ○Flag ○Turns ●Reactive        │
│         ○Active ○Lead                                   │
│  Cooldown: [45] seconds                                 │
│  Max per 10 min: [4]                                    │
│  Max words: [100]                                       │
│                                                         │
│  ── TRIGGERS ────────────────────────────────────────   │
│  ☑ Addressed directly        Priority: [1]              │
│  ☑ Factual error             Priority: [2]  Conf: [0.9] │
│  ☑ Contradiction             Priority: [3]              │
│  ☐ Circular discussion                                  │
│  ☐ Synthesis opportunity                                │
│  [+ Custom trigger]                                     │
│                                                         │
│  ── CONTRIBUTIONS ───────────────────────────────────   │
│  Enabled:                    Disabled:                   │
│  ☑ Factual correction        ☐ Redirect                 │
│  ☑ Opinion / position        ☐ Encouragement            │
│  ☑ Technical explanation     ☐ Time check               │
│  ☑ Risk flag                                            │
│  ☑ Alternative / suggestion                             │
│  ☑ Data / reference lookup                              │
│                                                         │
│  ── TONE ────────────────────────────────────────────   │
│  Preset: [None ▼]  (or set manually below)              │
│                                                         │
│  Formality:   Casual ───●─── Formal ─── Ceremonial     │
│  Directness:  Blunt ───●─── Diplomatic ─── Indirect    │
│  Warmth:      Reserved ●─── Neutral ─── Relational     │
│  Hedging:     Assertive ───●─── Cautious               │
│  Hierarchy:   Flat ●─── Aware ─── Deferential          │
│                                                         │
│  ── AUTHORITY ───────────────────────────────────────   │
│  ○Observer ○Advisor ○Peer ●Expert ○Chair                │
│                                                         │
│  ── POST-MEETING OUTPUTS ────────────────────────────   │
│  ☑ Structured notes          ☐ Executive summary        │
│  ☑ Action items by owner     ☐ Decision log             │
│  ☑ AI task list              ☑ Risk register            │
│  ☐ Follow-up agenda          ☐ Draft communications     │
│                                                         │
│  ── INTRODUCTION ────────────────────────────────────   │
│  ☑ Auto-generate from settings                          │
│  ☐ Custom: [...                                    ]    │
│                                                         │
│  ── PREVIEW ─────────────────────────────────────────   │
│  "I'm Synedrios, joining as technical advisor. I'll     │
│   speak up if I detect: factual error, contradiction.   │
│   After the meeting I'll produce: structured notes,     │
│   action items by owner, AI task list, risk register."  │
│                                                         │
│                              [Cancel]  [Save Role]      │
└─────────────────────────────────────────────────────────┘
```

#### 4.3 Meeting Setup

For starting a meeting from the GUI (alternative to Slack commands).

```
┌─────────────────────────────────────────────────────────┐
│  NEW MEETING                                            │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Title: [Q3 Architecture Review        ]                │
│  Channel: [#engineering ▼]                              │
│  Role: [Technical Advisor ▼]     [Customise for this    │
│                                   meeting only]         │
│                                                         │
│  ── MODIFIERS ───────────────────────────────────────   │
│  ☐ Agenda-bound     ☐ Time-strict                       │
│  ☑ Code-aware       ☐ Presentation-aware                │
│  ☐ Confidential     ☐ Learning mode                     │
│                                                         │
│  ── SIDE-CHANNEL ALERTS ─────────────────────────────   │
│  Alert recipient: [nikos@company.com    ]               │
│  Method: ☑ Slack DM  ☑ Email  ☐ SMS                    │
│  Rules: [Using defaults ▼]                              │
│                                                         │
│                              [Cancel]  [Start Meeting]  │
└─────────────────────────────────────────────────────────┘
```

#### 4.4 Live Monitor

Real-time view of an active meeting. WebSocket-driven updates.

```
┌─────────────────────────────────────────────────────────┐
│  LIVE: Q3 Architecture Review        [Silence AI] [End] │
├──────────────────────────────┬──────────────────────────┤
│  TRANSCRIPT                  │  ENGINE STATE            │
│                              │                          │
│  14:02 Nikos: I think we     │  Role: Tech Advisor      │
│  should migrate to PostgreSQL│  Initiative: Reactive    │
│  14:03 Sarah: But MongoDB    │  Contributions: 2        │
│  handles our current...      │  Cooldown: 12s remaining │
│  14:04 Mike: What about      │  Last trigger:           │
│  the query performance?      │    addressed_directly    │
│  14:05 SYNEDRIOS: The query  │    confidence: 1.0       │
│  patterns you described are  │                          │
│  heavily relational...       │  ── TRIGGERS EVALUATED ──│
│  14:06 Nikos: Good point.    │  addressed: ✓ (fired)   │
│  14:07 Sarah: Let me push    │  factual_error: ✗        │
│  back on that...             │  contradiction: ✗        │
│                              │                          │
│                              │  ── ATTENTION ──────────│
│                              │  Decisions: 0 captured   │
│                              │  Actions: 1 pending      │
│                              │  Open questions: 2       │
│                              │  Speakers: 3 active      │
│                              │                          │
│                              │  ── OVERRIDE ──────────│
│                              │  [Force Speak Now]       │
│                              │  [Change Role ▼]         │
│                              │  [Add Modifier ▼]        │
├──────────────────────────────┴──────────────────────────┤
│  Participants: Nikos (12 msgs) Sarah (8) Mike (5)       │
│  Duration: 34 min │ Messages: 47 │ AI: 3 contributions  │
└─────────────────────────────────────────────────────────┘
```

#### 4.5 Post-Meeting Review

Browse and edit outputs before distributing.

```
┌─────────────────────────────────────────────────────────┐
│  MEETING: Q3 Architecture Review          28 May 2026   │
│  Role: Technical Advisor │ Duration: 52 min │ 67 msgs   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [Notes] [Summary] [Actions] [Risks] [AI Tasks] [Audit] │
│  ────────────────────────────────────────────────────── │
│                                                         │
│  ACTION ITEMS BY OWNER                      [Edit] [✉]  │
│                                                         │
│  Nikos (3 items)                                        │
│  ☐ Review PostgreSQL migration plan (by 2 Jun)          │
│     Context: Agreed during database discussion at 14:23 │
│  ☐ Send benchmark results to Sarah                      │
│  ☐ Schedule follow-up with DevOps team                  │
│                                                         │
│  Sarah (2 items)                                        │
│  ☐ Draft data model comparison document                 │
│  ☐ Get budget approval for infrastructure change        │
│                                                         │
│  Mike (not present) (1 item)                            │
│  ☐ Update CI pipeline for new test suite                │
│                                                         │
│  Synedrios (2 items)                                    │
│  ☐ Research PostgreSQL partitioning strategies           │
│  ☐ Prepare Redis vs Memcached comparison                │
│                                                         │
│                    [Export Markdown] [Send to Slack]     │
└─────────────────────────────────────────────────────────┘
```

#### 4.6 Audit Browser

```
┌─────────────────────────────────────────────────────────┐
│  AUDIT LOG: Q3 Architecture Review                      │
│  Chain integrity: ✓ VALID (142 entries, verified)       │
├─────────────────────────────────────────────────────────┤
│  Filter: [All types ▼]  [All speakers ▼]  [Search...]   │
├─────────────────────────────────────────────────────────┤
│  14:00:01 MEETING_STARTED                               │
│    started_by: Nikos, role: technical_advisor            │
│  14:02:15 TRANSCRIPT_SEGMENT                            │
│    speaker: Nikos, "I think we should migrate..."       │
│  14:02:16 TRIGGER_SUPPRESSED                            │
│    trigger: factual_error, reason: cooldown             │
│  14:05:03 TRIGGER_FIRED                                 │
│    trigger: addressed_directly, conf: 1.0               │
│  14:05:04 CONTRIBUTION_MADE                             │
│    trigger: addressed_directly                          │
│    response: "The query patterns you described..."      │
│  ...                                                    │
│  14:52:00 MEETING_ENDED                                 │
│  14:52:15 POST_MEETING_OUTPUT                           │
│    type: structured_notes, length: 2847                 │
├─────────────────────────────────────────────────────────┤
│                              [Export JSON] [Export CSV]  │
└─────────────────────────────────────────────────────────┘
```

#### 4.7 Settings

```
┌─────────────────────────────────────────────────────────┐
│  SETTINGS                                               │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ── LLM CONFIGURATION ──────────────────────────────   │
│  Provider: [Anthropic ▼]                                │
│  Model: [claude-sonnet-4-20250514     ]                 │
│  API Key: [••••••••••••••••] [Show] [Test Connection]   │
│  Base URL: [https://api.anthropic.com ] (blank=default) │
│                                                         │
│  ── SLACK ───────────────────────────────────────────   │
│  Bot Token: [••••••••••••] [Test Connection]             │
│  App Token: [••••••••••••]                              │
│  Status: ● Connected                                    │
│                                                         │
│  ── PARTICIPANT ─────────────────────────────────────   │
│  Name: [Synedrios        ]                              │
│  Default Role: [Secretary ▼]                            │
│                                                         │
│  ── SIDE-CHANNEL ALERTS ─────────────────────────────   │
│  Default recipient: [nikos@company.com    ]             │
│  Default method: ☑ Slack DM  ☑ Email  ☐ SMS            │
│  [Manage alert rules...]                                │
│                                                         │
│  ── DATA ────────────────────────────────────────────   │
│  Database: SQLite (data/synedrios.db)                   │
│  Size: 12.4 MB │ Meetings: 47 │ Audit entries: 8,291   │
│  [Export all data] [Reset database]                     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### GUI Implementation Plan

| Phase | What Gets Built | Effort |
|-------|----------------|--------|
| v1.1a | Settings page, role list, role viewer (read-only) | 1 week |
| v1.1b | Role editor with full behaviour configuration | 1 week |
| v1.1c | Dashboard, meeting setup, post-meeting review | 1 week |
| v1.1d | Live monitor with WebSocket updates | 1 week |
| v1.1e | Audit browser with chain verification | 3 days |
| v1.1f | Polish, responsive design, error handling | 3 days |

Total: approximately 5 weeks for a solo developer with AI as the implementation layer.

---

## Part 5: Updated Version Roadmap

| Version | Focus | Key Deliverables |
|---------|-------|-------------------|
| **v1.0** (current) | Core engine, Slack bot | 5 roles, YAML config, CLI/Slack commands, SQLite, audit log |
| **v1.1** | Web GUI | Role builder, meeting setup wizard, live monitor, post-meeting review, audit browser, settings. All configuration moves from YAML to GUI (YAML remains as import/export format). |
| **v2.0** | Voice meetings | Recall.ai connector (Zoom/Teams/Meet), Deepgram STT, speaker diarisation, screenshot capture, PostgreSQL, RBAC, side-channel alerts, multiple LLM adapter testing. Additional roles: Devil's Advocate, Analyst, Creative Partner, Project Manager. |
| **v2.1** | Enterprise foundations | Multi-tenancy with row-level security, avatar ownership hierarchy (company/division/team/personal), OIDC/SAML authentication, encrypted secrets management. |
| **v3.0** | Full voice participant | TTS (ElevenLabs/Piper), turn-taking with silence detection, hand-raise protocol, semantic memory (ChromaDB), cross-meeting knowledge. Additional roles: Strategist, Translator, Compliance Monitor. |
| **v3.1** | Memory access control | Explicit memory sharing grants, per-domain access, revocation, audit trail for memory access. |
| **v4.0** | Cloud marketplace | AWS Marketplace listing, CloudFormation/Helm templates, usage metering. Azure and GCP to follow. Deployment templates for air-gapped VPC. |
| **v4.1** | Scale hardening | Meeting scheduler service, Redis session routing, horizontal pod autoscaling, 10,000 concurrent meeting target. |
