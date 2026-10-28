"""The single model<->tools loop used by both the prefix agent and the successor.

One *turn* is one assistant response, including any tool calls it makes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..llm.client import LLMClient
from ..sandbox.tools import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class LoopState:
    turns: int = 0
    last_usage: dict[str, int] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoopResult:
    messages: list[dict[str, Any]]
    tool_events: list[dict[str, Any]]
    final_answer: str | None
    finish_reason: str | None
    stopped_by: str  # final_answer | trigger | turn_limit
    turns: int
    usage: dict[str, int]
    reasoning: list[str | None]
    turn_ends: list[int] = field(default_factory=list)  # len(messages) after each turn (incl. its tool results)
    usage_per_turn: list[dict[str, int]] = field(default_factory=list)


async def run_agent_loop(
    client: LLMClient,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: ToolRegistry | None,
    max_turns: int,
    tag: str,
    should_stop: Callable[[LoopState], bool] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    after_turn: Callable[[LoopState], Awaitable[None]] | None = None,
    max_degenerate_retries: int = 2,
) -> LoopResult:
    """``max_degenerate_retries``: a turn that returns no tool calls and no content with
    finish_reason length/error (runaway reasoning hitting the output cap, or a provider-side
    error surfaced as a finish reason) is re-sampled up to this many times; if it persists the
    loop ends with ``stopped_by == "degenerate"`` and an empty final answer."""
    state = LoopState(messages=messages)
    tool_events: list[dict[str, Any]] = []
    reasoning: list[str | None] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    final_answer: str | None = None
    finish_reason: str | None = None
    stopped_by = "turn_limit"
    turn_ends: list[int] = []
    usage_per_turn: list[dict[str, int]] = []

    for _ in range(max_turns):
        degenerate = False
        for attempt in range(1 + max_degenerate_retries):
            resp = await client.complete(
                model=model,
                messages=messages,
                tools=tools.specs if tools is not None else None,
                tag=tag,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            degenerate = not resp.tool_calls and not (resp.content or "").strip() and resp.finish_reason in ("length", "error", None)
            if not degenerate:
                break
            if attempt < max_degenerate_retries:
                log.warning("[%s] %s: degenerate turn (finish=%s, no content, no tool calls); re-sampling %d/%d", tag, model, resp.finish_reason, attempt + 1, max_degenerate_retries)
        messages.append(resp.to_assistant_message())
        reasoning.append(resp.reasoning)
        state.turns += 1
        state.last_usage = resp.usage
        for k in usage_total:
            usage_total[k] += int(resp.usage.get(k, 0) or 0)
        finish_reason = resp.finish_reason
        usage_per_turn.append({k: int(resp.usage.get(k, 0) or 0) for k in usage_total})

        if not resp.tool_calls or tools is None:
            final_answer = resp.content or ""
            stopped_by = "degenerate" if degenerate else "final_answer"
            turn_ends.append(len(messages))
            break

        for tc in resp.tool_calls:
            result = await tools.execute(tc.name, tc.arguments)
            tool_events.append({"turn": state.turns, "id": tc.id, "name": tc.name, "arguments": tc.arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        turn_ends.append(len(messages))
        if after_turn is not None:
            await after_turn(state)

        if should_stop is not None and should_stop(state):
            stopped_by = "trigger"
            break

    return LoopResult(
        messages=messages,
        tool_events=tool_events,
        final_answer=final_answer,
        finish_reason=finish_reason,
        stopped_by=stopped_by,
        turns=state.turns,
        usage=usage_total,
        reasoning=reasoning,
        turn_ends=turn_ends,
        usage_per_turn=usage_per_turn,
    )
