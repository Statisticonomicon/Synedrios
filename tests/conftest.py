"""Shared pytest fixtures for Synedrios tests.

Provides: a temporary database, loaded mode configs, a mock LLM adapter,
and a fixture loader for meeting transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.engine.modes import load_all_modes
from src.llm.adapter import LLMAdapter
from src.models import EventType, MeetingEvent
from src.persistence.database import Database

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the --run-integration flag (integration tests hit real APIs)."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that make real API calls (costs money).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration-marked tests unless --run-integration is passed.

    This enforces the rule that no test makes a real network call unless it
    is explicitly marked `integration` and the operator opts in.
    """
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration (real API calls)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    """A fresh database in a temporary directory."""
    return Database(tmp_path / "test.db")


@pytest.fixture
def modes() -> dict:
    """All loaded mode configurations."""
    return load_all_modes("config/modes")


class MockLLMAdapter(LLMAdapter):
    """Mock LLM adapter returning canned responses for unit tests.

    Set `.next_response` to control what `complete()` returns.
    Inspect `.calls` to assert on what was sent.
    """

    def __init__(self, response: str = "Mock response.") -> None:
        self.next_response = response
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "mock/test"

    @property
    def context_window(self) -> int:
        return 200_000

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.next_response


@pytest.fixture
def mock_llm() -> MockLLMAdapter:
    """A mock LLM adapter for unit tests."""
    return MockLLMAdapter()


def load_fixture(name: str) -> list[MeetingEvent]:
    """Load a meeting transcript fixture by name (without .json extension).

    Fixture format: a JSON array of objects with keys
    speaker, content, and optionally event_type, confidence.
    """
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Fixture {name}.json not found. "
            f"Create it in {FIXTURE_DIR} (see test plan section 2.8)."
        )
    with open(path) as f:
        raw = json.load(f)
    return [
        MeetingEvent(
            event_type=EventType(item.get("event_type", "chat")),
            speaker=item["speaker"],
            content=item["content"],
            confidence=item.get("confidence", 1.0),
        )
        for item in raw
    ]


@pytest.fixture
def fixture_loader():
    """Provides the load_fixture function to tests."""
    return load_fixture
