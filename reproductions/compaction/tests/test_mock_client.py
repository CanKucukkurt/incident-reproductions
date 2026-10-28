from compaction_reader.llm.mock import ScriptedMockClient, turn_index_of


def test_turn_index_resets_at_user_message():
    msgs = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}, {"role": "tool"}, {"role": "assistant"}]
    assert turn_index_of(msgs) == 2
    assert turn_index_of(msgs + [{"role": "user"}]) == 0


async def test_script_then_default():
    c = ScriptedMockClient(script={"x": [{"tool_calls": [{"name": "list_files", "arguments": {}}]}]})
    r1 = await c.complete(model="m", messages=[{"role": "user", "content": "hi"}], tag="x")
    assert r1.tool_calls[0].name == "list_files" and r1.finish_reason == "tool_calls"
    r2 = await c.complete(model="m", messages=[{"role": "user", "content": "hi"}, r1.to_assistant_message()], tag="x")
    assert r2.content and r2.finish_reason == "stop"
    assert len(c.calls_for("x")) == 2
