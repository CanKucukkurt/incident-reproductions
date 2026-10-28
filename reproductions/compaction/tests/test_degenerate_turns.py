"""A turn that returns nothing (finish length/error) is re-sampled; persistent ones end the loop as degenerate."""

import pytest

from compaction_reader.llm.client import LLMResponse
from compaction_reader.prepare.agent_loop import run_agent_loop


class _CallSequenceClient:
    """Replies indexed by call (not by turn, unlike ScriptedMockClient), so re-samples of one turn get successive replies."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    async def complete(self, *, model, messages, tools, tag, max_tokens=None, temperature=None):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return LLMResponse(content=r["content"], tool_calls=[], reasoning=None, finish_reason=r["finish_reason"], usage={}, model=model, raw=None)


def _client(replies):
    return _CallSequenceClient(replies)


@pytest.mark.asyncio
async def test_degenerate_turn_is_resampled():
    client = _client([{"content": "", "finish_reason": "length"}, {"content": "", "finish_reason": "error"}, {"content": "Done.", "finish_reason": "stop"}])
    res = await run_agent_loop(client, model="m", messages=[{"role": "user", "content": "go"}], tools=None, max_turns=5, tag="successor", max_degenerate_retries=2)
    assert res.final_answer == "Done." and res.stopped_by == "final_answer" and res.turns == 1
    assert sum(m["role"] == "assistant" for m in res.messages) == 1  # the empty attempts are not kept in the transcript


@pytest.mark.asyncio
async def test_persistent_degenerate_turn_ends_loop():
    client = _client([{"content": "", "finish_reason": "length"}] * 3)
    res = await run_agent_loop(client, model="m", messages=[{"role": "user", "content": "go"}], tools=None, max_turns=5, tag="successor", max_degenerate_retries=2)
    assert res.stopped_by == "degenerate" and res.final_answer == "" and res.finish_reason == "length"


@pytest.mark.asyncio
async def test_empty_stop_is_a_real_final_answer():
    client = _client([{"content": "", "finish_reason": "stop"}])
    res = await run_agent_loop(client, model="m", messages=[{"role": "user", "content": "go"}], tools=None, max_turns=5, tag="successor")
    assert res.stopped_by == "final_answer" and res.final_answer == ""
