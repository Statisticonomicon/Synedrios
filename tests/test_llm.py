"""Unit tests for the LLM adapter layer (L01-L11).

All HTTP is mocked — no real network calls. Covers the provider factory,
local-LLM base URLs, request/response contracts for both adapters,
error propagation, context windows, and VPC base-URL overrides.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.llm.adapter import (
    AnthropicAdapter,
    OpenAIAdapter,
    create_adapter,
)


class FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(
        self, payload: dict, status_code: int = 200, raise_exc: Exception | None = None
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self._raise = raise_exc

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise


class CapturingClient:
    """Async HTTP client double that records calls and returns a canned response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def post(
        self, url: str, headers: dict | None = None, json: dict | None = None
    ) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response

    async def aclose(self) -> None:
        pass


# ----- Factory (L01-L05) -----


def test_l01_factory_anthropic() -> None:
    """L01 — provider=anthropic returns an AnthropicAdapter."""
    adapter = create_adapter({"provider": "anthropic", "api_key": "k"})
    assert isinstance(adapter, AnthropicAdapter)


def test_l02_factory_openai() -> None:
    """L02 — provider=openai returns an OpenAIAdapter."""
    adapter = create_adapter({"provider": "openai", "api_key": "k"})
    assert isinstance(adapter, OpenAIAdapter)


def test_l03_factory_ollama_base_url() -> None:
    """L03 — provider=ollama uses the correct local base_url."""
    adapter = create_adapter({"provider": "ollama"})
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter.base_url == "http://localhost:11434/v1"
    assert adapter.model == "llama3"


def test_l04_factory_vllm_base_url() -> None:
    """L04 — provider=vllm uses the correct local base_url."""
    adapter = create_adapter({"provider": "vllm"})
    assert adapter.base_url == "http://localhost:8000/v1"


def test_l05_factory_unknown_raises() -> None:
    """L05 — an unknown provider raises ValueError."""
    with pytest.raises(ValueError):
        create_adapter({"provider": "no_such_provider"})


# ----- Anthropic adapter (L06-L08) -----


@pytest.mark.asyncio
async def test_l06_anthropic_request_contract() -> None:
    """L06 — Anthropic.complete sends correct headers and body."""
    adapter = AnthropicAdapter(api_key="secret-key", model="claude-sonnet-5")
    adapter._client = CapturingClient(FakeResponse({"content": [{"type": "text", "text": "hi"}]}))
    await adapter.complete(
        messages=[{"role": "user", "content": "hello"}],
        system="be brief",
        max_tokens=123,
        temperature=0.5,
    )
    call = adapter._client.calls[0]
    assert call["url"].endswith("/v1/messages")
    assert call["headers"]["x-api-key"] == "secret-key"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["json"]["model"] == "claude-sonnet-5"
    assert call["json"]["max_tokens"] == 123
    # Sampling params are removed on Claude 5 / Opus 4.7+ models (400 if
    # sent); the adapter must swallow the argument, not forward it.
    assert "temperature" not in call["json"]
    assert call["json"]["system"] == "be brief"
    assert call["json"]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_l07_anthropic_extracts_text_blocks() -> None:
    """L07 — Anthropic.complete concatenates text content blocks."""
    adapter = AnthropicAdapter(api_key="k")
    adapter._client = CapturingClient(
        FakeResponse(
            {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "tool_use", "id": "x"},  # non-text block ignored
                    {"type": "text", "text": "second"},
                ]
            }
        )
    )
    result = await adapter.complete(messages=[{"role": "user", "content": "q"}])
    assert result == "first\nsecond"


@pytest.mark.asyncio
async def test_l08_anthropic_http_error_propagates() -> None:
    """L08 — Anthropic.complete propagates HTTP errors."""
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(429, text="rate limited", request=req)
    err = httpx.HTTPStatusError("429", request=req, response=resp)
    adapter = AnthropicAdapter(api_key="k")
    adapter._client = CapturingClient(FakeResponse({}, status_code=429, raise_exc=err))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.complete(messages=[{"role": "user", "content": "q"}])


# ----- OpenAI-compatible adapter (L09) -----


@pytest.mark.asyncio
async def test_l09_openai_sends_system_message() -> None:
    """L09 — OpenAI.complete prepends the system prompt as a system message."""
    adapter = OpenAIAdapter(api_key="k", model="gpt-4o")
    adapter._client = CapturingClient(
        FakeResponse({"choices": [{"message": {"content": "answer"}}]})
    )
    result = await adapter.complete(
        messages=[{"role": "user", "content": "q"}], system="you are terse"
    )
    assert result == "answer"
    sent = adapter._client.calls[0]["json"]["messages"]
    assert sent[0] == {"role": "system", "content": "you are terse"}
    assert sent[1] == {"role": "user", "content": "q"}
    # Authorization uses the bearer scheme
    assert adapter._client.calls[0]["headers"]["Authorization"] == "Bearer k"


# ----- Context window + overrides (L10-L11) -----


def test_l10_context_window_values() -> None:
    """L10 — context_window reports expected budgets per provider."""
    assert AnthropicAdapter(api_key="k").context_window == 200_000
    assert OpenAIAdapter(api_key="k").context_window == 128_000


def test_l11_base_url_override_for_vpc() -> None:
    """L11 — an explicit base_url overrides the default (air-gapped/VPC)."""
    adapter = create_adapter(
        {
            "provider": "anthropic",
            "api_key": "k",
            "base_url": "https://llm.internal.acme.local",
        }
    )
    assert adapter.base_url == "https://llm.internal.acme.local"
    # Trailing slash is normalised away
    adapter2 = create_adapter(
        {
            "provider": "openai",
            "api_key": "k",
            "base_url": "https://llm.internal.acme.local/",
        }
    )
    assert adapter2.base_url == "https://llm.internal.acme.local"


@pytest.mark.asyncio
async def test_l12_workspace_id_header_sent_when_configured(monkeypatch) -> None:
    """L12 — An identity-linked key's workspace id reaches the API as a header.

    Identity-linked Console keys 400 without `anthropic-workspace-id`;
    workspace-scoped keys need no header, so it must be strictly opt-in.
    """
    captured: dict = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["headers"] = headers

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"type": "text", "text": "ok"}]}

        return R()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with_ws = AnthropicAdapter(api_key="k", workspace_id="wrkspc_123")
    await with_ws.complete(messages=[{"role": "user", "content": "hi"}])
    assert captured["headers"]["anthropic-workspace-id"] == "wrkspc_123"

    without = AnthropicAdapter(api_key="k")
    await without.complete(messages=[{"role": "user", "content": "hi"}])
    assert "anthropic-workspace-id" not in captured["headers"]


def test_l13_factory_passes_workspace_id() -> None:
    """L13 — create_adapter forwards workspace_id to the Anthropic adapter."""
    adapter = create_adapter({"provider": "anthropic", "api_key": "k", "workspace_id": "wrkspc_9"})
    assert adapter.workspace_id == "wrkspc_9"
    assert create_adapter({"provider": "anthropic", "api_key": "k"}).workspace_id == ""
