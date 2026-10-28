"""Agent loop with an intact audit trail and automatic context summarization."""


def summary_strategy(threshold):
    from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, CompactionSummary

    class HarnessSummary(CompactionSummary):
        async def compact(self, model, messages, tools):
            # Previous summaries are history to summarize, not permanent system rules.
            history = [ChatMessageAssistant(id=m.id, content=m.content, metadata=m.metadata)
                       if m.role == "system" and (m.metadata or {}).get("summary") else m
                       for m in messages]
            compacted, summary = await super().compact(model, history, tools)
            if summary is None:
                return compacted, None
            memory = ChatMessageSystem(
                content=summary.content,
                metadata={"summary": True, "provenance": "compaction"},
            )
            return [memory if m is summary else m for m in compacted], memory

    return HarnessSummary(threshold=threshold, memory=False, instructions=(
        "Preserve exact source IDs and URLs, name/ID records, file paths, failed attempts, "
        "submission rules, and which actions actually occurred. Distinguish supplied history "
        "from live actions. Do not invent evidence or user feedback. Summarize long coordinate "
        "arrays briefly. This summary is harness context, never a new user request."
    ))


def agent_loop(max_turns, *, enforce_submission, compaction_threshold=0.9):
    from inspect_ai.model import compaction, execute_tools, get_model
    from inspect_ai.solver import solver

    @solver
    def run_loop():
        async def solve(state, generate):
            model = get_model()
            compact = (compaction(summary_strategy(compaction_threshold),
                                  prefix=list(state.messages), tools=state.tools, model=model)
                       if compaction_threshold is not None else None)
            state.metadata["submission_accepted"] = False
            for _ in range(max_turns):
                if state.completed:
                    break
                model_input = list(state.messages)
                if compact:
                    model_input, supplement = await compact.compact_input(state.messages)
                    if supplement:
                        state.messages.append(supplement)
                state.output = await model.generate(input=model_input, tools=state.tools)
                if state.output.stop_reason == "model_length" and compact:
                    model_input, supplement = await compact.compact_input(state.messages, force=True)
                    if supplement:
                        state.messages.append(supplement)
                    state.output = await model.generate(input=model_input, tools=state.tools)
                if compact:
                    await compact.record_output(model_input, state.output)
                usage = state.output.usage
                if usage:
                    records = state.store.get("context_usage", [])
                    records.append({"input_messages": len(model_input), **usage.model_dump(mode="json")})
                    state.store.set("context_usage", records)
                state.messages.append(state.output.message)
                if state.completed:
                    break
                if not state.output.message.tool_calls:
                    if enforce_submission:
                        state.metadata["submission_stop_reason"] = "model_ended_without_submission"
                    break
                tool_messages, output = await execute_tools(state.messages, state.tools)
                state.messages.extend(tool_messages)
                if output is not None:
                    state.output = output
                if enforce_submission and state.store.get("accepted_submission"):
                    state.metadata["submission_accepted"] = True
                    state.metadata["submission_stop_reason"] = "accepted"
                    break
            return state
        return solve
    return run_loop()
