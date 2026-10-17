"""Operator tooling for Synedrios.

Command-line utilities that support running and tuning the participant
against a real workspace (task T6). None of these are imported by the
runtime; they are operator tools.

    python -m tools.preflight    — check credentials and connectivity
    python -m tools.rehearse     — replay a transcript through the full stack
    python -m tools.calibrate    — analyse the audit log and tune thresholds
"""

from __future__ import annotations
