# Security Policy

## Reporting a Vulnerability

**Email:** security@synedrios.dev
**Response time:** Acknowledgement within 48 hours. Assessment within 7 days.

Please include:
- Clear description of the vulnerability and potential impact
- Steps to reproduce if possible
- Allow 90 days for a fix before public disclosure

## Scope

**In scope:** Application code, Docker image, default configuration,
dependencies, authentication/authorisation logic, data handling, audit
log integrity.

**Out of scope:** Third-party LLM APIs, third-party meeting platform
SDKs, customer infrastructure configuration.

## Severity and Response Times

| Severity | Examples | Target Fix |
|----------|---------|------------|
| Critical | Data exfiltration, auth bypass, code execution, audit tampering | 72 hours |
| High | Privilege escalation, cross-tenant access, memory access bypass | 7 days |
| Medium | Info disclosure (non-sensitive), DoS, config weaknesses | 30 days |
| Low | Hardening recommendations | Next release |

## Security Practices

- No secrets in code (all via environment variables)
- All input validated through Pydantic models
- SQLAlchemy ORM with parameterised queries (no raw SQL)
- Dependencies pinned to exact versions
- python:3.12-slim base image, minimal attack surface
- Container runs as non-root user
- No hardcoded external URLs — all endpoints customer-configured
- Audit log is append-only with hash chain integrity
- Automated vulnerability scanning via pip-audit and Trivy
