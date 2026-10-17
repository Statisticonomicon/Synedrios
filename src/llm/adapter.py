"""LLM adapter layer.

Provides a uniform interface for all LLM backends. Phase 1 ships with
the Anthropic Claude adapter. The adapter pattern means adding OpenAI,
Ollama, or any other backend is a single new class implementing the
same interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMAdapter(ABC):
    """Base interface for all LLM backends."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def context_window(self) -> int: ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """Send messages and return the assistant's text response."""
        ...


class AnthropicAdapter(LLMAdapter):
    """Claude API adapter via Anthropic's /v1/messages endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5",
        base_url: str = "https://api.anthropic.com",
        workspace_id: str = "",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Identity-linked Console API keys (as opposed to workspace-scoped
        # ones) require every request to name the workspace it acts in via the
        # anthropic-workspace-id header; without it the API returns 400.
        # Optional — workspace-scoped keys need no header.
        self.workspace_id = workspace_id
        self._client = httpx.AsyncClient(timeout=120.0)
        # Why the last response stopped ("end_turn", "max_tokens", ...). A
        # "max_tokens" here means the reply was cut off — on current models
        # adaptive thinking bills against the same budget, so a starved call
        # returns partial or empty text with no error. Logged loudly below;
        # kept on the adapter so tools and tests can assert on it.
        self.last_stop_reason: str = ""

    @property
    def name(self) -> str:
        return f"anthropic/{self.model}"

    @property
    def context_window(self) -> int:
        if "opus" in self.model or "sonnet" in self.model:
            return 200_000
        return 200_000  # conservative default

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """Call the Anthropic messages API and return the text response.

        `temperature` is accepted for interface compatibility but never sent:
        sampling parameters (temperature/top_p/top_k) are removed on the
        Claude 5 family and Opus 4.7+ — the API answers 400 "`temperature` is
        deprecated for this model". Call sites keep expressing their intent
        through the argument because OpenAI-compatible backends still honour it.
        """
        del temperature  # not part of the Anthropic request contract any more
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id

        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            self.last_stop_reason = str(data.get("stop_reason") or "")
            if self.last_stop_reason == "max_tokens":
                logger.warning(
                    "LLM response truncated at max_tokens=%d (model=%s) — "
                    "the reply is incomplete; raise the call's budget.",
                    max_tokens,
                    self.model,
                )
            # Extract text from content blocks
            text_parts = [
                block["text"] for block in data.get("content", []) if block.get("type") == "text"
            ]
            return "\n".join(text_parts)

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Anthropic API error %s: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise
        except Exception as exc:
            logger.error("LLM request failed: %s", exc)
            raise

    async def close(self) -> None:
        await self._client.aclose()


class OpenAIAdapter(LLMAdapter):
    """OpenAI-compatible API adapter. Works with OpenAI, Ollama, vLLM,
    LM Studio, or any endpoint that implements the OpenAI chat
    completions API format.

    For Ollama: base_url='http://localhost:11434/v1', api_key='ollama'
    For vLLM:   base_url='http://localhost:8000/v1', api_key='vllm'
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    @property
    def name(self) -> str:
        return f"openai-compat/{self.model}"

    @property
    def context_window(self) -> int:
        return 128_000  # conservative default

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": all_messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except Exception as exc:
            logger.error("OpenAI-compat request failed: %s", exc)
            raise

    async def close(self) -> None:
        await self._client.aclose()


def create_adapter(config: dict[str, Any]) -> LLMAdapter:
    """Factory: create the appropriate LLM adapter from config.

    Config format:
        provider: "anthropic" | "openai" | "ollama" | "vllm"
        api_key: "sk-..."
        model: "claude-sonnet-5"
        base_url: "https://api.anthropic.com"  (optional)
    """
    provider = config.get("provider", "anthropic").lower()
    api_key = config.get("api_key", "")
    model = config.get("model", "")
    base_url = config.get("base_url", "")

    if provider == "anthropic":
        return AnthropicAdapter(
            api_key=api_key,
            model=model or "claude-sonnet-5",
            base_url=base_url or "https://api.anthropic.com",
            workspace_id=config.get("workspace_id", ""),
        )
    elif provider in ("openai", "ollama", "vllm", "lmstudio"):
        defaults = {
            "openai": ("https://api.openai.com", "gpt-4o"),
            "ollama": ("http://localhost:11434/v1", "llama3"),
            "vllm": ("http://localhost:8000/v1", "default"),
            "lmstudio": ("http://localhost:1234/v1", "default"),
        }
        default_url, default_model = defaults[provider]
        return OpenAIAdapter(
            api_key=api_key or provider,
            model=model or default_model,
            base_url=base_url or default_url,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
