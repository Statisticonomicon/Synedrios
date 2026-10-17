"""Tests for the application entry point (src/__main__.py).

main() is the `python -m src` launcher. Its real logic is environment
validation and dependency wiring; the heavy dependencies (database,
Slack connector, event loop) are replaced with doubles so the wiring is
exercised without touching the network or the filesystem.
"""

from __future__ import annotations

import pytest

import src.__main__ as entry

REQUIRED = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY"]


class _FakeDB:
    db_path = "memory://test"

    def __init__(self, *a, **k) -> None:
        pass


class _FakeConnector:
    instances: list["_FakeConnector"] = []

    def __init__(self, *a, **k) -> None:
        self.kwargs = k
        _FakeConnector.instances.append(self)

    def start(self):  # called inside asyncio.run, which we stub out
        return "started"


@pytest.fixture(autouse=True)
def no_real_dotenv(monkeypatch) -> None:
    """main() loads .env on startup; tests must never read the developer's
    real .env (before this guard, the missing-env test picked up real Slack
    tokens and opened a live socket from inside the suite)."""
    monkeypatch.setattr(entry, "load_env_file", lambda *a, **k: 0)


@pytest.fixture
def all_env(monkeypatch) -> None:
    for var in REQUIRED:
        monkeypatch.setenv(var, "x")


def test_missing_env_exits_1(monkeypatch) -> None:
    """Missing required environment variables cause a clean exit(1)."""
    for var in REQUIRED:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1


def test_no_modes_exits_1(monkeypatch, all_env) -> None:
    """If no meeting modes load, the launcher exits(1)."""
    monkeypatch.setattr(entry, "Database", _FakeDB)
    monkeypatch.setattr(entry, "load_all_modes", lambda _dir: {})
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1


def test_happy_path_starts_connector(monkeypatch, all_env) -> None:
    """With env and modes present, main wires up and starts the connector."""
    _FakeConnector.instances.clear()
    started: list = []
    monkeypatch.setattr(entry, "Database", _FakeDB)
    monkeypatch.setattr(entry, "load_all_modes", lambda _dir: {"secretary": object()})
    monkeypatch.setattr(entry, "SlackConnector", _FakeConnector)
    monkeypatch.setattr(entry.asyncio, "run", lambda coro: started.append(coro))

    entry.main()

    assert len(_FakeConnector.instances) == 1
    assert _FakeConnector.instances[0].kwargs["participant_name"] == "Synedrios"
    assert started == ["started"]


# --------------------------------------------------------------------------
# .env loading (E01-E04) — bare-metal runs must behave like Docker env_file
# --------------------------------------------------------------------------


def test_e01_env_file_loads_and_strips(tmp_path, monkeypatch):
    """E01 — KEY=VALUE, quotes, export prefix, comments, blanks."""
    from src.envfile import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "SYN_TEST_A=plain\n"
        'SYN_TEST_B="double quoted"\n'
        "SYN_TEST_C='single quoted'\n"
        "export SYN_TEST_D=exported\n"
        "SYN_TEST_E=sk-ant-with#hash\n"
    )
    for k in ("SYN_TEST_A", "SYN_TEST_B", "SYN_TEST_C", "SYN_TEST_D", "SYN_TEST_E"):
        monkeypatch.delenv(k, raising=False)

    import os

    assert load_env_file(env) == 5
    assert os.environ["SYN_TEST_A"] == "plain"
    assert os.environ["SYN_TEST_B"] == "double quoted"
    assert os.environ["SYN_TEST_C"] == "single quoted"
    assert os.environ["SYN_TEST_D"] == "exported"
    assert os.environ["SYN_TEST_E"] == "sk-ant-with#hash"  # no inline-comment stripping
    for k in ("SYN_TEST_A", "SYN_TEST_B", "SYN_TEST_C", "SYN_TEST_D", "SYN_TEST_E"):
        monkeypatch.delenv(k, raising=False)


def test_e02_real_environment_wins(tmp_path, monkeypatch):
    """E02 — a variable already set is never overridden by the file."""
    from src.envfile import load_env_file

    monkeypatch.setenv("SYN_TEST_WINS", "from-real-env")
    env = tmp_path / ".env"
    env.write_text("SYN_TEST_WINS=from-file\n")

    import os

    assert load_env_file(env) == 0
    assert os.environ["SYN_TEST_WINS"] == "from-real-env"


def test_e03_missing_file_is_not_an_error(tmp_path):
    """E03 — Docker/CI set real env vars; no .env is a silent no-op."""
    from src.envfile import load_env_file

    assert load_env_file(tmp_path / "does-not-exist.env") == 0


def test_e04_malformed_lines_are_skipped(tmp_path, monkeypatch):
    """E04 — junk lines don't crash startup or poison the environment."""
    from src.envfile import load_env_file

    monkeypatch.delenv("SYN_TEST_OK", raising=False)
    env = tmp_path / ".env"
    env.write_text("not a pair\n=novalue-key\nSYN_TEST_OK=yes\n")

    import os

    assert load_env_file(env) == 1
    assert os.environ["SYN_TEST_OK"] == "yes"
    monkeypatch.delenv("SYN_TEST_OK", raising=False)
