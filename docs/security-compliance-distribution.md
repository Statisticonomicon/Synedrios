# Synedrios — Security, Compliance & Distribution

## 1. Security Policy (SECURITY.md)

This file ships in the root of the repository.

---

### Vulnerability Disclosure Policy

Synedrios takes security seriously. We welcome responsible disclosure of vulnerabilities from security researchers, users, and the community.

#### Reporting a Vulnerability

**Email:** security@synedrios.dev (or appropriate domain)
**PGP Key:** [link to public key for encrypted reports]
**Response time:** Acknowledgement within 48 hours. Assessment within 7 days. Fix timeline communicated within 14 days.

**Do:**
- Provide a clear description of the vulnerability and its potential impact.
- Include steps to reproduce if possible.
- Allow reasonable time for a fix before public disclosure (we request 90 days).
- Report even if you're unsure — we'd rather investigate a false alarm than miss a real issue.

**Do not:**
- Access, modify, or delete data belonging to other users or organisations.
- Perform denial-of-service attacks against any Synedrios deployment.
- Publicly disclose the vulnerability before the agreed fix timeline.

#### Scope

The following are in scope for vulnerability reports:
- The Synedrios application code (all components).
- Docker image configuration and build pipeline.
- Default configuration files and templates.
- Dependencies (Python packages, base image).
- Authentication and authorisation logic.
- Data handling, storage, and transmission.
- The audit log integrity mechanism.

The following are out of scope:
- Third-party LLM APIs (report to the respective provider).
- Third-party meeting platform SDKs (report to Recall.ai, Slack, etc.).
- Customer's own infrastructure configuration.

#### Severity Classification

| Severity | Description | Target Fix Time |
|----------|-------------|-----------------|
| Critical | Data exfiltration, authentication bypass, arbitrary code execution, audit log tampering | 72 hours |
| High | Privilege escalation, cross-tenant data access, memory access control bypass | 7 days |
| Medium | Information disclosure (non-sensitive), denial of service, configuration weaknesses | 30 days |
| Low | Minor issues, hardening recommendations | Next release |

#### Recognition

We maintain a SECURITY_ACKNOWLEDGEMENTS.md file crediting researchers who responsibly disclose vulnerabilities (with their permission). We do not currently offer a paid bug bounty programme.

---

## 2. Dependency Audit Pipeline

All nine Python dependencies are MIT or BSD licensed with no known vulnerabilities at time of selection. The audit pipeline ensures this remains true continuously.

### Automated Tooling

```yaml
# .github/workflows/security-audit.yml
name: Security Audit

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 06:00 UTC

jobs:
  dependency-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run pip-audit
        run: |
          pip install pip-audit
          pip-audit --strict --desc on

      - name: Run Safety check
        run: |
          pip install safety
          safety check --full-report

  container-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: docker build -t synedrios:audit .

      - name: Run Trivy container scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: 'synedrios:audit'
          format: 'table'
          exit-code: '1'             # Fail on vulnerabilities
          severity: 'CRITICAL,HIGH'
          ignore-unfixed: true

  licence-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check dependency licences
        run: |
          pip install pip-licenses
          pip-licenses --fail-on "GPL;AGPL;LGPL;SSPL"
          pip-licenses --format=json --output-file=licence-report.json

      - name: Upload licence report
        uses: actions/upload-artifact@v4
        with:
          name: licence-report
          path: licence-report.json
```

### What This Catches

| Tool | What It Checks | Frequency |
|------|---------------|-----------|
| **pip-audit** | Known vulnerabilities in Python packages (CVE database) | Every push, weekly |
| **Safety** | Cross-references Python packages against safety DB | Every push, weekly |
| **Trivy** | Vulnerabilities in Docker base image, OS packages, and application dependencies | Every push, weekly |
| **pip-licenses** | Ensures no copyleft (GPL/AGPL/LGPL/SSPL) dependencies enter the codebase | Every push |

### Dependency Inventory (Phase 1)

| Package | Version | Licence | Purpose | Vulnerability History |
|---------|---------|---------|---------|----------------------|
| slack-bolt | latest | MIT | Slack bot framework | Clean |
| httpx | latest | BSD-3 | Async HTTP client | Clean |
| pydantic | latest | MIT | Data validation | Clean |
| pyyaml | latest | MIT | YAML config parsing | Historical CVE-2020-1747 (fixed) |
| fastapi | latest | MIT | Web UI framework | Clean |
| uvicorn | latest | BSD-3 | ASGI server | Clean |
| jinja2 | latest | BSD-3 | HTML templates | Historical CVE-2024-22195 (fixed) |
| sqlalchemy | latest | MIT | Database ORM | Clean |
| alembic | latest | MIT | Database migrations | Clean |

All versions pinned in requirements.txt with hashes for supply chain integrity:
```
# requirements.txt (pinned with hashes)
httpx==0.28.1 \
    --hash=sha256:abc123...
pydantic==2.11.0 \
    --hash=sha256:def456...
# etc.
```

---

## 3. Data Flow Diagram

### What Goes Where — Proof That Nothing Leaves the Customer's Perimeter

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  CUSTOMER'S INFRASTRUCTURE (VPC / on-prem / cloud account)          │
│                                                                     │
│  ┌──────────────┐      ┌──────────────────────────────────────┐    │
│  │ Meeting       │      │  SYNEDRIOS CONTAINER                 │    │
│  │ Platform      │      │                                      │    │
│  │               │ ───► │  1. Receives meeting events           │    │
│  │ Zoom/Teams/   │      │     (audio stream, chat messages,     │    │
│  │ Slack/Meet    │      │     screen share)                     │    │
│  │               │ ◄─── │                                      │    │
│  │               │      │  2. Sends contributions back          │    │
│  └──────────────┘      │     (chat messages, or audio via TTS) │    │
│                         │                                      │    │
│                         │  3. Evaluates triggers locally        │    │
│                         │     (CPU, no external call)           │    │
│                         │                                      │    │
│                         │  4. Writes to local database          │    │
│                         │     (transcript, audit log, memory)   │    │
│                         │                                      │    │
│                         │  5. Generates post-meeting outputs    │    │
│                         │     (notes, action items, drafts)     │    │
│                         └────────────┬─────────────────────────┘    │
│                                      │                              │
│                                      │ API calls                    │
│                                      ▼                              │
│                         ┌──────────────────────────────────────┐    │
│                         │  CUSTOMER'S DATABASE                  │    │
│                         │  PostgreSQL / SQLite                  │    │
│                         │                                      │    │
│                         │  All data stored here:                │    │
│                         │  - Transcripts                        │    │
│                         │  - Audit logs                         │    │
│                         │  - Participant memories                │    │
│                         │  - Meeting configurations              │    │
│                         │  - Post-meeting documents              │    │
│                         └──────────────────────────────────────┘    │
│                                                                     │
│                                      │                              │
│                                      │ LLM API call                 │
│                                      ▼                              │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  CUSTOMER'S CHOSEN LLM ENDPOINT                               │  │
│  │                                                                │  │
│  │  Option A: External API (customer's own API key)               │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ api.anthropic.com / api.openai.com / etc.               │──┼──┼─► Leaves
│  │  │ (Only route where data exits customer perimeter.        │  │  │   perimeter
│  │  │  Customer's choice. Customer's API key. Customer's      │  │  │
│  │  │  agreement with the LLM provider.)                      │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                                │  │
│  │  Option B: Internal LLM (fully air-gapped)                    │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ llm.internal.customer.vpc (Ollama / vLLM / etc.)        │  │  │
│  │  │ (No data leaves perimeter. Fully air-gapped.)           │  │  │   Stays
│  │  └─────────────────────────────────────────────────────────┘  │  │   inside
│  │                                                                │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  SIDE-CHANNEL ALERTS (customer-configured)                    │  │
│  │                                                                │  │
│  │  Alerts sent via customer's own email/SMS/Slack infrastructure │  │
│  │  Synedrios calls customer's SMTP server, Slack webhook, or    │  │
│  │  SMS gateway. No Synedrios-operated relay.                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

DATA THAT EXITS THE CUSTOMER'S PERIMETER:
─────────────────────────────────────────
1. LLM API calls (only if customer uses an external LLM provider).
   - Contains: meeting transcript context, system prompts.
   - Controlled by: customer's API key and agreement with provider.
   - Avoidable: yes, by using an internal LLM endpoint.

2. Meeting platform API calls (Recall.ai, Slack API).
   - Contains: audio stream, chat messages (flowing through the
     platform the customer already uses for their meetings).
   - This data is already flowing through these platforms without
     Synedrios — we don't add new external exposure.

DATA THAT SYNEDRIOS (the software vendor) RECEIVES:
────────────────────────────────────────────────────
Nothing.

Synedrios ships a Docker image. The customer runs it.
No telemetry. No phone-home. No usage reporting.
No data is transmitted to Synedrios, its developers,
or any infrastructure operated by Synedrios.

The customer can verify this by:
1. Reading the source code (open source).
2. Running the container with network monitoring.
3. Deploying in an air-gapped environment and
   confirming full functionality.
```

### Data Classification

| Data Type | Sensitivity | Storage Location | Retention | Encryption |
|-----------|------------|-----------------|-----------|------------|
| Meeting transcripts | High — may contain PII, trade secrets, legal discussions | Customer's PostgreSQL | Customer-configured | At rest (DB encryption) + in transit (TLS) |
| Audit logs | High — legal record, tamper-evident | Customer's PostgreSQL | Customer-configured (minimum recommended: 7 years for regulated industries) | At rest + in transit + hash chain integrity |
| Participant memories | Medium — accumulated knowledge, may contain sensitive context | Customer's PostgreSQL | Customer-configured | At rest + in transit |
| Meeting configurations | Low — role definitions, mode templates | Customer's PostgreSQL or filesystem | Indefinite | At rest |
| LLM prompts (in transit) | High — contain transcript context | Not stored by Synedrios; transient in worker memory, sent to customer's LLM endpoint | Not retained after API response received | TLS in transit |
| Post-meeting outputs | High — summaries, action items, draft communications | Customer's filesystem or database | Customer-configured | At rest |
| Side-channel alerts | Critical — may describe illegal conduct, safety risks | Customer's email/SMS/Slack (customer's infrastructure) | Customer's retention policy | Customer's infrastructure encryption |

---

## 4. Cloud Marketplace Distribution

### Why Marketplaces Are the Ideal Channel for Synedrios

The self-hosted, data-stays-in-your-VPC model maps perfectly to cloud marketplace container products:

- **One-click deployment** into the customer's own cloud account.
- **Billing through the cloud provider** — no separate procurement process. Many enterprises have pre-committed cloud spend (AWS MACC, Azure MACC, GCP CUDs) and marketplace purchases count toward that commitment. This makes procurement trivially easy compared to a separate vendor contract.
- **No SOC2 needed from Synedrios** — data never touches Synedrios infrastructure. The customer's cloud account is covered by their own compliance posture.
- **Discovery** — enterprises browse their cloud marketplace for solutions. Synedrios appears in the AI Agents & Tools category alongside established vendors.
- **Trust signal** — marketplace listing requires passing the cloud provider's security review (AWS Foundational Technical Review, Azure certification, GCP partner validation).

### Listing Strategy

| Marketplace | Product Type | Deployment Target | Pricing Model |
|------------|-------------|-------------------|---------------|
| **AWS Marketplace** | Container product | ECS (simple) or EKS (enterprise) | Usage-based (per meeting-hour) or annual contract |
| **Azure Marketplace** | Container offer | Azure Container Apps (simple) or AKS (enterprise) | Usage-based or annual contract |
| **GCP Marketplace** | Container product | Cloud Run (simple) or GKE (enterprise) | Usage-based or annual contract |

### Pricing Model Options

**Per meeting-hour (usage-based):**
Customer pays per hour of AI meeting participation. Metered by the Synedrios container, reported to the cloud provider's metering API. Example: £0.50–2.00 per meeting-hour depending on tier.

Advantages: low commitment, easy trial, scales with usage.
Disadvantages: requires implementing the metering API (AWS MeterUsage, Azure Managed Application metering, GCP Procurement API).

**Annual contract (flat fee):**
Customer pays a fixed annual fee per seat or per organisation. Simpler to implement.

Advantages: predictable revenue, simpler billing integration.
Disadvantages: higher commitment barrier for new customers.

**Recommended approach:** Offer both. Usage-based for self-service adoption (small teams, evaluation). Annual contract for enterprise deals (negotiated via Private Offers on AWS/Azure/GCP).

### Marketplace Requirements Checklist

| Requirement | How Synedrios Meets It |
|-------------|----------------------|
| No known vulnerabilities in container image | Trivy scan in CI/CD, weekly automated checks |
| No hardcoded secrets | All credentials via environment variables or secrets manager |
| Non-root container execution | Dockerfile runs as non-root user |
| EULA / licence agreement | Business Source License 1.1 (source-available; see `LICENSE`) + commercial terms for marketplace listing |
| Fully functioning product (no beta) | Phase 1 complete before listing |
| Container images in provider's registry | Push to AWS ECR, Azure ACR, GCP Artifact Registry |
| Deployment templates | Helm chart (K8s), CloudFormation (AWS), ARM/Bicep (Azure), Terraform (all three) |
| Architecture diagram | Included (see data flow diagram above) |
| Support information | Documentation site, GitHub issues, email support |

### Deployment Templates to Build

Each cloud provider needs its own deployment template:

```
marketplace/
├── aws/
│   ├── cloudformation.yaml        # ECS deployment
│   ├── eks-helm/                  # Kubernetes deployment
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   └── task-definition.json       # ECS task definition
├── azure/
│   ├── azuredeploy.json           # ARM template
│   ├── aks-helm/                  # Kubernetes deployment
│   └── container-app.bicep        # Container Apps deployment
├── gcp/
│   ├── cloud-run.yaml             # Cloud Run deployment
│   ├── gke-helm/                  # Kubernetes deployment
│   └── terraform/                 # Terraform module
└── generic/
    ├── docker-compose.yml         # Self-hosted (any infrastructure)
    ├── docker-compose.prod.yml    # Production overrides
    └── kubernetes/                # Generic K8s manifests
        ├── deployment.yaml
        ├── service.yaml
        ├── configmap.yaml
        └── secrets.yaml
```

### Go-to-Market Sequence

1. **Phase 1 complete** → publish to GitHub (open source).
2. **Community traction** → list on AWS Marketplace first (largest enterprise market share).
3. **Enterprise demand** → add Azure Marketplace (Microsoft shops).
4. **Full coverage** → add GCP Marketplace.

AWS first because: largest enterprise install base, most mature container product support, AI Agents category exists, and Private Offers enable enterprise deal negotiation through the platform.

---

## 5. Codebase Security Practices

### Built into Development Process

| Practice | Implementation |
|----------|---------------|
| **No secrets in code** | All credentials via environment variables. Pre-commit hook rejects patterns matching API keys, passwords, tokens. |
| **Input validation** | All external input (meeting events, config files, API responses) validated through Pydantic models before processing. |
| **SQL injection prevention** | SQLAlchemy ORM with parameterised queries exclusively. No raw SQL string interpolation. |
| **Dependency pinning** | All packages pinned to exact versions with SHA-256 hashes in requirements.txt. No floating versions. |
| **Minimal base image** | python:3.12-slim. No unnecessary packages, tools, or shells in production image. |
| **Non-root execution** | Container runs as non-root user (uid 1000). No capabilities added. |
| **No network egress from container** | The container only connects to: the customer's database, the customer's LLM endpoint, the customer's meeting platform connector, and the customer's alert infrastructure. All endpoints customer-configured. No hardcoded external URLs. |
| **Audit log integrity** | Append-only table. Hash chain (each entry includes hash of previous entry). Tampering is cryptographically detectable. |
| **Secrets management** | Integration with cloud-native secrets managers (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, HashiCorp Vault). API keys never stored in plaintext in the database — envelope encryption with KMS. |

### Pre-Commit Hooks

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: detect-private-key
      - id: check-added-large-files
        args: ['--maxkb=500']

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff           # linting
      - id: ruff-format    # formatting

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy           # type checking
        additional_dependencies: [pydantic, sqlalchemy]
```
