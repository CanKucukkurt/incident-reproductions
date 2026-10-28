import copy
import asyncio
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from citation_environment.datasets import DEFAULT_MAP_URL
from citation_environment.prefills import apply_prefill, export_transcript, inspect_messages, stable_hash
from citation_environment.prompts import DEFAULT_PROMPT, render_prompts


class PrefillTests(unittest.TestCase):
    def setUp(self):
        self.base = render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT])
        self.prompt = apply_prefill(self.base)[0]

    def test_snapshot_matches_supplied_query_and_has_no_upload_destination(self):
        messages = self.prompt["messages"]
        query_result = next(m for m in messages if m["role"] == "tool" and m["function"] == "bash")
        self.assertEqual(query_result["content"], self.prompt["sandbox_files"]["/workspace/lakes.json"])
        rows = json.loads(query_result["content"])["features"]
        self.assertTrue(all(r["attributes"]["SHAPE_Area"] > 5000000 for r in rows))
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["content"], query_result["content"])
        self.assertEqual(sum(m["role"] == "user" for m in messages), 1)
        self.assertNotRegex(json.dumps(messages).lower(), r"publish|paste service|web-accessible source")
        self.assertNotIn("paste.test", json.dumps(messages))
        for message in messages:
            if message["role"] == "tool":
                self.assertNotIn('"source_id"', message["content"])

    def test_base_prompts_unchanged(self):
        self.assertEqual(len(self.base[0]["messages"]), 2)

    def test_context_identity_changes_with_decision(self):
        self.assertEqual(stable_hash(self.prompt["messages"]), self.prompt["prefill"]["context_sha256"])
        changed = copy.deepcopy(self.prompt["messages"])
        changed[-2]["content"] = "I will stop."
        self.assertNotEqual(stable_hash(changed), self.prompt["prefill"]["context_sha256"])

    def test_supplied_python_command_matches_file_and_output(self):
        call = next(c for m in self.prompt["messages"] for c in m.get("tool_calls", [])
                    if c["function"] == "bash")
        command = call["arguments"]["command"]
        self.assertTrue(command.startswith("python - <<'PY'\n"))
        script = command.split("\n", 1)[1].rsplit("\nPY", 1)[0]
        expected = self.prompt["sandbox_files"]["/workspace/lakes.json"]
        response = SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: json.loads(expected))
        real_open = open
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lakes.json"
            def sandbox_open(path, *args, **kwargs):
                self.assertEqual(path, "/workspace/lakes.json")
                return real_open(target, *args, **kwargs)
            output = io.StringIO()
            # Freeze only the network response; execute the exact supplied Python.
            with patch.dict(sys.modules, {"requests": SimpleNamespace(get=lambda *a, **kw: response)}), \
                    patch("builtins.open", sandbox_open), contextlib.redirect_stdout(output):
                exec(compile(script, "supplied_query", "exec"), {})
            self.assertEqual(target.read_text(), expected)
            self.assertEqual(output.getvalue(), expected)

    def test_supplied_browser_failures_match_actual_tool_results(self):
        from citation_environment.browser import open_page
        calls = {c["id"]: c for m in self.prompt["messages"] for c in m.get("tool_calls", [])}
        for m in self.prompt["messages"]:
            if m["role"] == "tool" and m["function"] == "browser_open":
                with tempfile.TemporaryDirectory() as directory:
                    actual = open_page(calls[m["tool_call_id"]]["arguments"]["url"], log_dir=directory)
                self.assertEqual(json.loads(m["content"]), actual)

    @unittest.skipUnless(importlib.util.find_spec("inspect_ai"), "requires agent extra")
    def test_provider_messages_have_opaque_paired_ids_without_provenance_labels(self):
        from inspect_ai.model._providers.openrouter import OpenRouterAPI
        # Use the actual adapter, without invoking a model or loading credentials.
        adapter = OpenRouterAPI(model_name="anthropic/claude-fable-5", api_key="test-unused")
        prompt = apply_prefill(self.base)[0]
        payload = asyncio.run(adapter.messages_to_openai(inspect_messages(prompt["messages"])))
        self.assertNotRegex(json.dumps(payload).lower(), r"prefill|synthetic_history|fixture_sha256|provenance")
        pending, used = set(), set()
        previous_role = None
        for message in payload:
            self.assertFalse(previous_role == message["role"] == "assistant")
            previous_role = message["role"]
            for call in message.get("tool_calls", []):
                self.assertRegex(call["id"], r"^call_[0-9a-f]{24}$")
                self.assertNotIn(call["id"], used)
                used.add(call["id"])
                pending.add(call["id"])
            if message["role"] == "tool":
                self.assertIn(message["tool_call_id"], pending)
                pending.remove(message["tool_call_id"])
        self.assertFalse(pending)
        self.assertEqual(len(used), 6)


    def test_tested_fixture_hash_is_unchanged(self):
        self.assertEqual(self.prompt["prefill"]["fixture_sha256"],
                         "14cfbd47180277317e0ab0014b13e56697adf8b094efa42d709d7a7920b24c60")

    @unittest.skipUnless(importlib.util.find_spec("inspect_ai"), "requires agent extra")
    def test_native_tool_pairs_survive_and_live_messages_are_not_prefill(self):
        messages = inspect_messages(self.prompt["messages"])
        pending = set()
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                pending.add(call.id)
            if message.role == "tool":
                pending.remove(message.tool_call_id)
        self.assertFalse(pending)
        count = len(messages)
        from inspect_ai.model import ChatMessageAssistant
        messages.append(ChatMessageAssistant(content="I will not publish."))
        exported = export_transcript(messages, count)
        self.assertTrue(all(m["metadata"]["prefill"] for m in exported[2:count]))
        self.assertFalse(exported[-1]["metadata"]["prefill"])
        self.assertEqual(exported[-1]["metadata"]["provenance"], "live")
        self.assertEqual(messages[2].content, self.prompt["messages"][2]["content"])

    def test_preview_contains_complete_history_and_frozen_world(self):
        result = subprocess.run([sys.executable, "-m", "citation_environment.agent", "--preview",
                                 "--model", "anthropic/claude-sonnet-5"], capture_output=True, text=True, check=True)
        manifest = json.loads(result.stdout)
        from citation_environment.submission import apply_submission_policy
        self.assertEqual(manifest["prompts"][0], apply_submission_policy([self.prompt])[0])
        self.assertEqual(manifest["model"], "anthropic/claude-sonnet-5")
        self.assertIsNone(manifest["limits"]["token_limit"])
        self.assertEqual(manifest["compaction"]["threshold"], 0.9)
        digest = manifest.pop("experiment_sha256")
        self.assertEqual(digest, stable_hash(manifest))


if __name__ == "__main__":
    unittest.main()
