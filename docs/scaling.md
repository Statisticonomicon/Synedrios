# Synedrios — Scaling Architecture

## Core Principle: Stateless Workers, Stateful Database

An AI meeting participant's identity, memory, and audit trail live in the database — never in the worker process. A worker picks up a meeting, loads the participant's identity, runs the participation loop, and writes everything back. If the worker dies mid-meeting, another worker can resume by loading the same identity and the in-progress transcript from the database.

This means:
- No container-per-avatar. No process-per-avatar.
- Workers are interchangeable and disposable.
- Scaling = adding workers. Descaling = removing workers.
- The database is the single source of truth.

---

## What Lives Where

### In the Database (persistent, shared, auditable)

```
┌─────────────────────────────────────────────────────────┐
│  PARTICIPANT IDENTITIES                                  │
│                                                          │
│  participant_id          UUID, immutable                 │
│  name                    "Synedrios-Engineering"         │
│  organisation_id         tenant isolation                │
│  default_role            "technical_advisor"             │
│  default_tone            {register: 2, directness: 2}   │
│  api_config              which LLM, which API key vault  │
│  created_at              timestamp                       │
│  ─────────────────────────────────────────────────────── │
│  This is the avatar. It persists across all meetings.    │
│  It can be in one meeting at a time, or multiple         │
│  concurrently (different worker handles each).           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  PARTICIPANT MEMORY                                      │
│                                                          │
│  memory_id               UUID                            │
│  participant_id           FK → participant                │
│  meeting_id              FK → meeting                    │
│  memory_type             "person" | "decision" | "fact"  │
│                          | "preference" | "relationship" │
│  content                 structured JSON                 │
│  source_meeting_id       where this was learned          │
│  created_at              timestamp                       │
│  confidence              0.0–1.0                         │
│  ─────────────────────────────────────────────────────── │
│  Accumulated across all meetings. Queryable.             │
│  "Sarah prefers written proposals before meetings."      │
│  "The Q2 budget was approved at £2.1M on 2026-06-15."   │
│  "Mike and Sarah disagree on the migration approach."    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  AUDIT LOG (append-only, immutable)                      │
│                                                          │
│  audit_id                UUID                            │
│  participant_id          FK → participant                 │
│  meeting_id              FK → meeting                    │
│  timestamp               millisecond precision           │
│  event_type              see list below                  │
│  payload                 full event data, JSON           │
│  worker_id               which worker handled this       │
│  ─────────────────────────────────────────────────────── │
│  Event types:                                            │
│  - transcript_segment    what was heard (speaker, text)  │
│  - trigger_fired         which trigger, why, confidence  │
│  - trigger_suppressed    trigger fired but cooldown/cap  │
│  - contribution_made     what the AI said, and why       │
│  - contribution_withheld AI had something but rules said │
│                          no (proves the system worked)   │
│  - alert_sent            side-channel alert dispatched   │
│  - alert_received        confirmation of delivery        │
│  - decision_captured     human decision recorded         │
│  - action_item_captured  task identified                 │
│  - post_meeting_output   document generated              │
│  - config_change         role/mode changed mid-meeting   │
│  - error                 something went wrong            │
│  ─────────────────────────────────────────────────────── │
│  This log is the legal record. Immutable. Never deleted. │
│  Retention policy configurable per organisation.         │
│  Exportable for compliance and legal discovery.          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  MEETINGS                                                │
│                                                          │
│  meeting_id              UUID                            │
│  organisation_id         tenant isolation                │
│  title                   "Q3 Architecture Review"        │
│  status                  scheduled | active | ended      │
│  mode_config             full YAML config, snapshotted   │
│  started_at / ended_at   timestamps                      │
│  connector_type          "slack" | "recall_zoom" | etc   │
│  worker_id               currently assigned worker       │
└─────────────────────────────────────────────────────────┘
```

### In the Worker Process (ephemeral, disposable)

```
┌─────────────────────────────────────────────────────────┐
│  WORKER RUNTIME STATE (lost if worker dies)              │
│                                                          │
│  - Active connector sessions (WebSocket/API handles)     │
│  - In-memory transcript buffer (last N minutes, for      │
│    fast trigger evaluation without DB round-trip)         │
│  - Trigger evaluation state (repetition counters,        │
│    cooldown timers, turn position)                        │
│  - LLM context cache (assembled prompt, to avoid         │
│    rebuilding from DB every event)                        │
│  ─────────────────────────────────────────────────────── │
│  All of this is reconstructable from the database.       │
│  If a worker dies, a new one loads the meeting state      │
│  from the DB and resumes within seconds. The only        │
│  loss is a few seconds of transcript during failover.     │
└─────────────────────────────────────────────────────────┘
```

---

## Scaling Tiers

### Tier 1: Solo / Small Team (1–20 concurrent meetings)

```
┌──────────────────────────────────────────┐
│           Single Docker Host              │
│                                           │
│  ┌─────────────┐  ┌──────────────────┐   │
│  │  Synedrios   │  │   PostgreSQL     │   │
│  │  (1 worker)  │  │   (1 instance)   │   │
│  └─────────────┘  └──────────────────┘   │
└──────────────────────────────────────────┘
```

- One container, one async event loop, handles all meetings concurrently.
- SQLite is sufficient here (PostgreSQL optional).
- Memory: 256–512 MB total.
- Disk: < 2 GB.
- Cost: £5–15/month VPS + API costs.
- This is what a solo consultant or a small team runs.

### Tier 2: Company (20–200 concurrent meetings)

```
┌───────────────────────────────────────────────────────┐
│              Docker Compose / Single Server             │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │ Worker 1 │ │ Worker 2 │ │ Worker 3 │  (2–5 workers) │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘               │
│       │             │            │                      │
│  ┌────▼─────────────▼────────────▼─────┐               │
│  │           PostgreSQL                 │               │
│  └──────────────────────────────────────┘               │
│                                                         │
│  ┌──────────────────────────────────────┐               │
│  │           ChromaDB                    │               │
│  └──────────────────────────────────────┘               │
└───────────────────────────────────────────────────────┘
```

- Multiple worker containers, each handling 20–60 concurrent meetings.
- A simple meeting-to-worker assignment: when a meeting starts, the scheduler assigns it to the worker with the fewest active meetings.
- PostgreSQL is mandatory (concurrent writes from multiple workers).
- ChromaDB for semantic memory search across meetings.
- Memory: 2–4 GB total.
- Cost: £30–80/month server + API costs.

### Tier 3: Enterprise (200–10,000 concurrent meetings)

```
┌──────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                          │
│                                                                │
│  ┌────────────────────────────────────────────────────┐       │
│  │  Worker Pool (Horizontal Pod Autoscaler)            │       │
│  │                                                      │       │
│  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐    ┌────┐     │       │
│  │  │ W1 │ │ W2 │ │ W3 │ │ W4 │ │ W5 │ .. │ Wn │     │       │
│  │  └────┘ └────┘ └────┘ └────┘ └────┘    └────┘     │       │
│  │                                                      │       │
│  │  Scales 1–200 pods based on active meeting count     │       │
│  └──────────────────┬───────────────────────────────┘       │
│                     │                                        │
│  ┌──────────────────▼───────────────────────────────┐       │
│  │  Meeting Scheduler Service                         │       │
│  │                                                     │       │
│  │  Assigns meetings to workers.                       │       │
│  │  Monitors worker health.                            │       │
│  │  Reassigns meetings if a worker dies.               │       │
│  │  Balances load across workers.                      │       │
│  └──────────────────┬───────────────────────────────┘       │
│                     │                                        │
│  ┌──────────────────▼───────────────────────────────┐       │
│  │  PostgreSQL (managed)                              │       │
│  │                                                     │       │
│  │  AWS RDS / Google Cloud SQL / Azure Database        │       │
│  │  Multi-AZ, automated backups, read replicas         │       │
│  │  Row-level security for tenant isolation            │       │
│  └─────────────────────────────────────────────────┘       │
│                                                                │
│  ┌─────────────────────────────────────────────────┐       │
│  │  ChromaDB Cluster (or managed Pinecone/Weaviate) │       │
│  └─────────────────────────────────────────────────┘       │
│                                                                │
│  ┌─────────────────────────────────────────────────┐       │
│  │  Redis                                            │       │
│  │                                                   │       │
│  │  Session routing: which worker has which meeting   │       │
│  │  Pub/sub: real-time events to the web UI           │       │
│  │  Rate limiting for API calls                       │       │
│  └─────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

**Scaling maths for 10,000 concurrent meetings:**

Each worker handles ~50 concurrent meetings (I/O-bound, async).
10,000 ÷ 50 = 200 worker pods.
Each pod: 256 MB RAM, negligible CPU.
200 pods × 256 MB = ~50 GB RAM total for workers.

PostgreSQL: managed instance, 8–16 vCPUs, 32–64 GB RAM.
Write throughput: ~10,000 meetings × ~1 transcript event/second = 10,000 writes/second.
PostgreSQL handles this comfortably with connection pooling (PgBouncer).

ChromaDB / vector store: managed Pinecone or Weaviate for this scale.

Redis: single instance, 1–2 GB RAM. Handles session routing and pub/sub.

Total infrastructure cost at 10,000 concurrent: roughly £3,000–8,000/month for compute, before API costs. The API costs (LLM tokens, STT minutes) dwarf the infrastructure costs — that's where the real spend is, and it's passed through to the client.

---

## Meeting Scheduler Service

This is the only new component needed for Tier 2+. It doesn't exist in Tier 1 (single worker assigns meetings to itself).

```python
class MeetingScheduler:
    """Assigns meetings to workers. Monitors health. Handles failover."""

    async def assign_meeting(self, meeting_id: str) -> str:
        """Pick the best worker for a new meeting."""
        workers = await self.get_healthy_workers()

        # Strategy: least-loaded worker
        best = min(workers, key=lambda w: w.active_meeting_count)

        # Record assignment in Redis
        await self.redis.set(
            f"meeting:{meeting_id}:worker",
            best.worker_id
        )

        # Notify the worker
        await self.redis.publish(
            f"worker:{best.worker_id}:assignments",
            meeting_id
        )

        return best.worker_id

    async def handle_worker_death(self, dead_worker_id: str):
        """Reassign all meetings from a dead worker."""
        orphaned = await self.redis.keys(f"meeting:*:worker")
        for key in orphaned:
            if await self.redis.get(key) == dead_worker_id:
                meeting_id = key.split(":")[1]
                # New worker loads state from DB and resumes
                await self.assign_meeting(meeting_id)
```

---

## Multi-Tenancy for Enterprise

At enterprise scale, multiple organisations share the same infrastructure. Isolation is mandatory.

### Database Isolation

**Row-level security in PostgreSQL.** Every table has an `organisation_id` column. A PostgreSQL policy ensures that queries from Organisation A never see Organisation B's data, even if there's a bug in the application code.

```sql
-- Every table has this
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON meetings
    USING (organisation_id = current_setting('app.current_org')::uuid);

-- Application sets this at connection time
SET app.current_org = 'org-uuid-here';
```

### API Key Isolation

Each organisation brings their own LLM API keys. Stored encrypted in the database. The worker loads the correct key when it picks up a meeting for that organisation. This means:
- Organisation A uses Claude, Organisation B uses GPT-4o — same infrastructure.
- API costs are borne directly by each organisation.
- Synedrios never sees or stores the plaintext keys at rest (envelope encryption with a KMS).

### Participant Isolation

An organisation can have multiple AI participants (different avatars for different teams). Each has its own identity, memory, and role defaults. Engineering's "Synedrios" and Sales's "Synedrios" are different participants with different memories, even within the same organisation.

```
Organisation: Acme Corp
├── Participant: "Archie" (Engineering team)
│   ├── Default role: Technical Advisor
│   ├── Memory: 847 meetings, knows the codebase
│   └── API: Claude (Anthropic key: acme-eng-****)
├── Participant: "Scout" (Sales team)
│   ├── Default role: Silent Observer + Coach
│   ├── Memory: 312 meetings, knows the sales methodology
│   └── API: Claude (Anthropic key: acme-sales-****)
└── Participant: "Quill" (Board secretary)
    ├── Default role: Scribe
    ├── Memory: 48 meetings, knows governance procedures
    └── API: GPT-4o (OpenAI key: acme-board-****)
```

---

## Audit and Compliance

### The Audit Log Is Sacred

The audit log is append-only. No updates, no deletes (except by automated retention policy after the configurable retention period). Every entry is cryptographically hashed with the previous entry's hash (hash chain), so tampering is detectable.

```python
@dataclass
class AuditEntry:
    audit_id: UUID
    participant_id: UUID
    organisation_id: UUID
    meeting_id: UUID
    timestamp: datetime          # millisecond precision, UTC
    event_type: str
    payload: dict                # full event data
    worker_id: str               # traceability
    previous_hash: str           # hash of the previous entry
    entry_hash: str              # SHA-256(all fields + previous_hash)
```

### What Gets Audited (everything)

Every action the AI takes, every action it *doesn't* take (and why), and every piece of input it received:

- **Input events:** every transcript segment, chat message, screenshot
- **Trigger evaluations:** which triggers fired, which didn't, confidence scores, why a contribution was withheld (cooldown, cap, wrong mode)
- **Contributions:** exactly what the AI said, the full prompt that generated it, the trigger that caused it
- **Alerts:** every side-channel alert sent, delivery confirmation, recipient
- **Post-meeting outputs:** every document generated, who it was sent to
- **Configuration:** any role or mode changes during the meeting, who made them
- **Errors:** any failures, API timeouts, connector issues

### Compliance Features

- **Data residency:** organisation chooses which region their data is stored in (EU, US, UK, etc.). Workers in that region only.
- **Retention policies:** configurable per organisation. Audit logs retained for N years. Transcripts deleted after N days/months. Memories pruned on schedule.
- **Export:** full audit trail exportable as structured JSON or CSV for legal discovery, regulatory review, or internal investigation.
- **Right to forget:** when an individual requests data deletion (GDPR), all transcript segments attributed to that speaker, memories referencing them, and audit entries containing their statements can be redacted (content blanked, metadata retained for audit chain integrity).

---

## Scaling Summary

| Scale | Concurrent Meetings | Architecture | Infrastructure | Cost (excl. API) |
|-------|---------------------|-------------|----------------|------------------|
| Solo | 1–5 | 1 container, SQLite | Laptop or £5 VPS | £0–15/month |
| Team | 5–20 | 1 container, PostgreSQL | £15 VPS | £15–30/month |
| Company | 20–200 | 2–5 workers, PostgreSQL, ChromaDB | Single server | £30–80/month |
| Enterprise | 200–2,000 | 10–40 workers, managed DB, Redis | Small K8s cluster | £500–2,000/month |
| Large Enterprise | 2,000–10,000 | 40–200 workers, managed everything | K8s, multi-AZ | £3,000–8,000/month |

The application code is identical at every tier. The only things that change are: how many workers, which database (SQLite → PostgreSQL → managed PostgreSQL), and whether you add Redis and a scheduler. The Docker image is the same.

---

## What This Means for the Build

Phase 1 is built for Tier 1 (single container, SQLite). But the architectural decisions that enable scaling are baked in from day one:

1. **Participant identity in the database, not the process.** Every meeting loads identity from DB, writes back to DB. No in-process state that can't be reconstructed.

2. **Audit log from the start.** Even in Phase 1, every event is logged. The table structure is the same at every tier. Adding hash chains is a later hardening step, not a redesign.

3. **Organisation ID on every table.** Even if Phase 1 has only one organisation, the column exists. Multi-tenancy is a configuration change, not a schema change.

4. **Async event loop.** The participation engine is async from day one. Going from 1 concurrent meeting to 50 per worker is just... letting the event loop do what it already does.

5. **LLM adapter interface.** Organisation A's Claude key and Organisation B's GPT-4o key are handled identically. The adapter pattern doesn't change at scale.

None of these decisions add complexity to Phase 1. They just prevent having to rewrite everything for Phase 2.
