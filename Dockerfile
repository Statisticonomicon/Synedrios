FROM python:3.12-slim AS base

# Security: run as non-root user
RUN groupadd -r synedrios && useradd -r -g synedrios -m synedrios

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies — hash-locked for supply-chain integrity.
# --require-hashes makes pip refuse any package whose artifact hash is missing
# or does not match requirements.txt.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

# Application code
COPY src/ /app/src/
COPY config/ /app/config/

# Operator tooling — preflight, rehearse, calibrate. The T6 field-test loop
# (preflight before each session, calibrate after each meeting) must be
# runnable where the meeting database lives; in a container that is here:
#   docker compose exec synedrios python -m tools.preflight
#   docker compose exec synedrios python -m tools.calibrate --db data/synedrios.db
COPY tools/ /app/tools/

WORKDIR /app

# Data directory (mounted as volume for persistence)
RUN mkdir -p /app/data && chown synedrios:synedrios /app/data

USER synedrios

ENTRYPOINT ["python", "-m", "src"]
