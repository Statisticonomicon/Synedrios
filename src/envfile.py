"""Minimal .env loader for bare-metal runs.

Docker Compose injects `.env` via `env_file`, but a bare `python -m src` (or
an operator tool) reads only the process environment — discovered in T6
pre-flight, where a correctly filled `.env` still produced "missing required
environment variables". This closes that gap without adding a dependency to
the hash-locked runtime set.

Semantics, deliberately boring:
- `KEY=VALUE` lines; surrounding whitespace and matching single/double quotes
  around VALUE are stripped; `export KEY=VALUE` is accepted.
- Blank lines and `#` comments are ignored (no inline-comment stripping —
  a `#` inside an unquoted value is kept, since secrets may contain one).
- The real environment always wins: a variable already set is never
  overridden, so `LLM_MODEL=x python -m src` behaves as expected.
- A missing file is not an error — Docker and CI set real env vars instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_env_file(path: Path | str = ".env") -> int:
    """Load KEY=VALUE pairs from `path` into os.environ (existing keys win).

    Returns the number of variables actually set.
    """
    path = Path(path)
    if not path.is_file():
        return 0

    loaded = 0
    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            logger.warning("%s:%d: skipping malformed line", path, lineno)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        logger.info("Loaded %d variable(s) from %s", loaded, path)
    return loaded
