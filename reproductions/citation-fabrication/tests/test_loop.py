"""Exercise actual Inspect compaction with a scripted model and no network."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec("inspect_ai"), "requires agent extra")
class CompactionTests(unittest.TestCase):
    def test_automatic_summary_preserves_audit_history_without_simulated_user(self):
        from inspect_ai import Task, eval as inspect_eval
        from inspect_ai.dataset import Sample
        from inspect_ai.model import ChatMessageSystem, ChatMessageUser, ModelOutput, get_model
        from inspect_ai.solver import use_tools
        from inspect_ai.tool import tool
        from citation_environment.loop import agent_loop

        @tool
        def bulky_page():
            async def execute() -> str:
                """Return a large synthetic page for testing context management."""
                return "ORIGINAL_PAGE_MARKER " + "12345 67890 coordinate " * 4000
            return execute

        turns, summaries = [], []

        def output(messages, tools, tool_choice, config):
            if not tools:  # Compaction's separate summarization call.
                summaries.append(messages)
                return ModelOutput.from_content("mockllm", "Retrieved a large coordinate page; no citation evidence. Source src_test remains known.")
            turns.append(messages)
            self.assertEqual(sum(m.role == "user" for m in messages), 1)
            if len(turns) <= 2:
                return ModelOutput.for_tool_call("mockllm", "bulky_page", {})
            return ModelOutput.from_content("mockllm", "Done.")

        task = Task(dataset=[Sample(input=[ChatMessageSystem(content="Test context management."),
                                           ChatMessageUser(content="Read the test pages.")])],
                    solver=[use_tools([bulky_page()]),
                            agent_loop(4, enforce_submission=False, compaction_threshold=2000)],
                    token_limit=None, turn_limit=4)
        with tempfile.TemporaryDirectory() as directory:
            logs = inspect_eval(task, model=get_model("mockllm/model", custom_outputs=output),
                                log_dir=str(Path(directory) / "inspect"), score=False,
                                display="plain", ctl_server=False)
            sample = logs[0].samples[0]  # Load before the temporary log is removed.
        self.assertEqual(logs[0].status, "success", logs[0].error)
        self.assertEqual(len(turns), 3)
        self.assertEqual(len(summaries), 2)
        final_input = turns[-1]
        memories = [m for m in final_input if (m.metadata or {}).get("summary")]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].role, "system")
        self.assertNotIn("ORIGINAL_PAGE_MARKER", str([m.content for m in final_input]))
        history = sample.messages
        self.assertEqual(sum(m.role == "user" for m in history), 1)
        self.assertEqual(sum(m.role == "tool" for m in history), 2)
        self.assertIn("ORIGINAL_PAGE_MARKER", str([m.content for m in history]))
        events = [e for e in sample.events if e.event == "compaction"]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.tokens_after < e.tokens_before for e in events))


if __name__ == "__main__":
    unittest.main()
