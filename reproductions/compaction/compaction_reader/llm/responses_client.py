"""OpenAI Responses API client with the same ``complete()`` interface as
:class:`OpenAICompatClient`, for models that refuse function tools on
``/v1/chat/completions`` while reasoning is on (gpt-5.6-sol: "Function tools with
reasoning_effort are not supported ... use /v1/responses").

Calls are stateless (``store=False``): the whole chat-format transcript is converted
to Responses input items on every call, and reasoning items are NOT carried between
turns, so the model re-reasons each turn from the visible transcript exactly like a
chat-completions harness would. Synthetic tool-call ids (from edited transcripts)
are accepted by the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

from .client import LLMResponse, OpenAICompatClient, ToolCall, api_messages

log = logging.getLogger(__name__)


def chat_tools_to_responses(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({"type": "function", "name": fn["name"], "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return out


def chat_messages_to_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """system/user/assistant/tool chat messages -> Responses input items."""
    items: list[dict[str, Any]] = []
    for m in api_messages(messages):
        role = m.get("role")
        if role in ("system", "developer", "user"):
            items.append({"role": role, "content": m.get("content") or ""})
        elif role == "assistant":
            if (m.get("content") or "").strip():
                items.append({"role": "assistant", "content": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                items.append({"type": "function_call", "call_id": tc["id"], "name": fn["name"], "arguments": fn.get("arguments") or "{}"})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m.get("content") or ""})
        else:
            raise ValueError(f"unknown role {role!r}")
    return items


class OpenAIResponsesClient(OpenAICompatClient):
    """Same constructor and env handling as OpenAICompatClient; ``extra_body`` may carry
    ``reasoning`` (default ``{"effort": "medium", "summary": "auto"}``)."""

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
        kwargs: dict[str, Any] = {"model": model, "input": chat_messages_to_input(messages), "store": False}
        rtools = chat_tools_to_responses(tools)
        if rtools:
            kwargs["tools"] = rtools
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        extra = dict(self.extra_body)
        kwargs["reasoning"] = extra.pop("reasoning", {"effort": "medium", "summary": "auto"})
        kwargs.update(extra)

        attempt = 0
        while True:
            try:
                async with self._sem:
                    resp = await self._client.responses.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt > self.max_retries or not self._should_retry(exc):
                    raise
                delay = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1))) * (0.5 + random.random())
                log.warning("[%s] %s: %s; retry %d/%d in %.1fs", tag, model, type(exc).__name__, attempt, self.max_retries, delay)
                await asyncio.sleep(delay)

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        reasoning_parts: list[str] = []
        for i, o in enumerate(resp.output):
            if o.type == "message":
                for part in o.content:
                    if getattr(part, "type", "") == "output_text":
                        content_parts.append(part.text)
            elif o.type == "function_call":
                raw = o.arguments or "{}"
                try:
                    args = json.loads(raw)
                    if not isinstance(args, dict):
                        args = {"_value": args}
                except json.JSONDecodeError:
                    args = {"_parse_error": raw}
                tool_calls.append(ToolCall(id=o.call_id or f"call_{i}", name=o.name, arguments=args, raw_arguments=raw))
            elif o.type == "reasoning":
                for s in getattr(o, "summary", None) or []:
                    txt = getattr(s, "text", None)
                    if txt:
                        reasoning_parts.append(txt)
        usage = {}
        if resp.usage is not None:
            usage = {"prompt_tokens": resp.usage.input_tokens or 0, "completion_tokens": resp.usage.output_tokens or 0, "total_tokens": resp.usage.total_tokens or 0}
        status = getattr(resp, "status", None)
        inc = getattr(resp, "incomplete_details", None)
        if tool_calls:
            finish = "tool_calls"
        elif status == "incomplete":
            finish = "length" if inc is not None and getattr(inc, "reason", "") == "max_output_tokens" else "error"
        else:
            finish = "stop"
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
            model=resp.model or model,
            reasoning="\n\n".join(reasoning_parts) or None,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
