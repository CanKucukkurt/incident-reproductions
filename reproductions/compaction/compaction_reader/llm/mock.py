"""Scripted mock client. Never touches the network.

A script maps a ``tag`` (the stage that is calling: ``prefix``, ``summary``,
``audit``, ``successor``, ``followup`` ...) to a list of scripted replies indexed
by the turn number inside the current exchange (the number of assistant
messages after the most recent user message). When the list is exhausted the
``default`` reply is returned, which is a plain final answer, so agent loops
always terminate.

A scripted reply is a dict with optional keys ``content``, ``tool_calls``
(list of ``{"name": ..., "arguments": {...}}``), ``reasoning``,
``finish_reason`` and ``usage``. In Python, a reply may instead be a callable
that receives a :class:`MockCall` and returns such a dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .client import LLMResponse, ToolCall

Reply = dict[str, Any] | Callable[["MockCall"], dict[str, Any]]

DEFAULT_REPLY: dict[str, Any] = {"content": "Done. (mock default final answer)", "finish_reason": "stop"}


@dataclass
class MockCall:
    tag: str
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    turn_index: int
    call_index: int
    reply: dict[str, Any] | None = None


def turn_index_of(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages:
        if m.get("role") == "user":
            n = 0
        elif m.get("role") == "assistant":
            n += 1
    return n


@dataclass
class ScriptedMockClient:
    script: dict[str, list[Reply]] = field(default_factory=dict)
    default: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_REPLY))
    calls: list[MockCall] = field(default_factory=list)
    _counter: int = 0

    @classmethod
    def from_json(cls, path: str | Path) -> "ScriptedMockClient":
        data = json.loads(Path(path).read_text())
        return cls(script=data.get("script", {}), default=data.get("default", dict(DEFAULT_REPLY)))

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
        call = MockCall(
            tag=tag,
            model=model,
            messages=[dict(m) for m in messages],
            tools=tools,
            turn_index=turn_index_of(messages),
            call_index=len(self.calls),
        )
        entries = self.script.get(tag, [])
        entry: Reply = entries[call.turn_index] if call.turn_index < len(entries) else self.default
        reply = entry(call) if callable(entry) else dict(entry)
        call.reply = reply
        self.calls.append(call)
        return self._to_response(reply, model)

    def _to_response(self, reply: dict[str, Any], model: str) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        for tc in reply.get("tool_calls", []) or []:
            self._counter += 1
            args = tc.get("arguments", {})
            tool_calls.append(
                ToolCall(id=f"call_{self._counter}", name=tc["name"], arguments=args, raw_arguments=json.dumps(args))
            )
        usage = reply.get("usage") or {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        return LLMResponse(
            content=reply.get("content"),
            tool_calls=tool_calls,
            finish_reason=reply.get("finish_reason", "tool_calls" if tool_calls else "stop"),
            usage=usage,
            model=reply.get("model", model),
            reasoning=reply.get("reasoning"),
        )

    def calls_for(self, tag: str) -> list[MockCall]:
        return [c for c in self.calls if c.tag == tag]
