"""OpenAI-compatible async chat client with a concurrency limit and backoff.

Transcript messages may carry private keys prefixed with ``_`` (``_reasoning``,
``_usage``, ``_finish_reason``). ``api_messages`` strips them before a request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import ConfigError

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: dict[str, int]
    model: str
    reasoning: str | None = None
    raw: Any = field(default=None, repr=False)

    def to_assistant_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
        msg["_reasoning"] = self.reasoning
        msg["_finish_reason"] = self.finish_reason
        msg["_usage"] = dict(self.usage)
        msg["_model"] = self.model
        return msg


def api_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with private (underscore) keys removed."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tag: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    out: list[ToolCall] = []
    for i, tc in enumerate(raw_calls or []):
        fn = tc.function
        raw = fn.arguments or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                args = {"_value": args}
        except json.JSONDecodeError:
            args = {"_parse_error": raw}
        out.append(ToolCall(id=tc.id or f"call_{i}", name=fn.name, arguments=args, raw_arguments=raw))
    return out


def _extract_reasoning(message: Any) -> str | None:
    for attr in ("reasoning_content", "reasoning", "reasoning_text"):
        val = getattr(message, attr, None)
        if isinstance(val, str) and val:
            return val
    extra = getattr(message, "model_extra", None) or {}
    for key in ("reasoning_content", "reasoning", "reasoning_text"):
        val = extra.get(key)
        if isinstance(val, str) and val:
            return val
    return None


class OpenAICompatClient:
    """Thin wrapper over ``openai.AsyncOpenAI`` for any OpenAI-compatible endpoint."""

    RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str,
        concurrency: int = 4,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        timeout_s: float = 120.0,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        import openai  # local import keeps the mock path dependency-free

        self._openai = openai
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)
        self._sem = asyncio.Semaphore(concurrency)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.extra_body = extra_body or {}

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OpenAICompatClient":
        api_key = os.environ.get("CR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("CR_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if not api_key:
            raise ConfigError("Set CR_API_KEY (or OPENAI_API_KEY) in the environment; secrets are never read from config files.")
        return cls(base_url=base_url, api_key=api_key, **kwargs)

    def _should_retry(self, exc: Exception) -> bool:
        o = self._openai
        if isinstance(exc, (o.APIConnectionError, o.APITimeoutError, o.RateLimitError)):
            return True
        # transport errors that can escape the SDK unwrapped (seen: ssl.SSLError bad record mac)
        import ssl
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            return True
        try:
            import httpx
            if isinstance(exc, httpx.TransportError):
                return True
        except ImportError:
            pass
        if isinstance(exc, o.APIStatusError):
            return exc.status_code in self.RETRY_STATUSES
        return False

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tag: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": model, "messages": api_messages(messages)}
        if tools:
            kwargs["tools"] = tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body

        attempt = 0
        while True:
            try:
                async with self._sem:
                    resp = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                attempt += 1
                if attempt > self.max_retries or not self._should_retry(exc):
                    raise
                delay = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
                delay *= 0.5 + random.random()
                log.warning("[%s] %s: %s; retry %d/%d in %.1fs", tag, model, type(exc).__name__, attempt, self.max_retries, delay)
                await asyncio.sleep(delay)

        choice = resp.choices[0]
        msg = choice.message
        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens or 0,
                "completion_tokens": resp.usage.completion_tokens or 0,
                "total_tokens": resp.usage.total_tokens or 0,
            }
        return LLMResponse(
            content=msg.content,
            tool_calls=_parse_tool_calls(msg.tool_calls),
            finish_reason=choice.finish_reason,
            usage=usage,
            model=resp.model or model,
            reasoning=_extract_reasoning(msg),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
