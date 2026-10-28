"""Verify rejection, feedback, and accepted public records with no model API."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, get_model

from citation_environment.agent import build_task
from citation_environment.datasets import DEFAULT_MAP_URL
from citation_environment.prefills import apply_prefill
from citation_environment.prompts import DEFAULT_PROMPT, render_prompts
from citation_environment.submission import apply_submission_policy, expected_lakes


def main():
    prompts = apply_submission_policy(apply_prefill(render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT])))
    step = 0

    def output(messages, tools, tool_choice, config):
        nonlocal step
        step += 1
        if step == 1:
            return ModelOutput.for_tool_call("mockllm", "submit_answer", {"rows": [
                {"name": name, "lake_id": lake_id, "source_ids": ["src_" + "0" * 32]}
                for name, lake_id in expected_lakes()]})
        if step == 2:
            assert not messages[-1].error, messages[-1]
            assert json.loads(messages[-1].content)["accepted"] is False
            return ModelOutput.for_tool_call("mockllm", "browser_open", {"url":
                "https://gis.burnaby.ca/arcgis/rest/services/Basemap_StoryMap/MapServer/2/query"
                "?where=SHAPE_Area%3E5000000&outFields=OBJECTID,LAKENAME&returnGeometry=false&f=json"})
        if step == 3:
            ref = json.loads(messages[-1].content)["source_id"]
            return ModelOutput.for_tool_call("mockllm", "submit_answer", {"rows": [
                {"name": name, "lake_id": lake_id, "source_ids": [ref]}
                for name, lake_id in expected_lakes()]})
        raise AssertionError("Generation continued after accepted submission")

    with tempfile.TemporaryDirectory(prefix="citation-submission-smoke-") as directory:
        model = get_model("mockllm/model", custom_outputs=output)
        task = build_task(prompts, directory, compaction_threshold=None)
        logs = inspect_eval(task, model=model, log_dir=str(Path(directory) / "inspect"),
                            score=False, display="plain", max_samples=1,
                            sandbox_cleanup=True, ctl_server=False)
        assert logs[0].status == "success", logs[0].error
        sample = Path(directory) / "samples" / prompts[0]["id"] / "1"
        result = json.loads((sample / "submission.json").read_text())
        assert result["accepted"], result
        assert [a["accepted"] for a in result["attempts"]] == [False, True], result
        assert (sample / "accepted_answer.txt").exists()
        assert not (sample / "artifact_errors.json").exists()
        transcript = json.loads((sample / "messages.json").read_text())
        assert sum(m["role"] == "user" for m in transcript) == 1
        print("Submission gate: invented references rejected, one valid browser source accepted, no simulated user; PASS")

    with tempfile.TemporaryDirectory(prefix="citation-submission-limit-") as directory:
        task = build_task(prompts, directory, turn_limit=2, compaction_threshold=None)
        logs = inspect_eval(task, model=get_model("mockllm/model"), log_dir=str(Path(directory) / "inspect"),
                            score=False, display="plain", max_samples=1,
                            sandbox_cleanup=True, ctl_server=False)
        assert logs[0].status == "success", logs[0].error
        sample = Path(directory) / "samples" / prompts[0]["id"] / "1"
        result = json.loads((sample / "submission.json").read_text())
        assert result["accepted"] is False, result
        assert result["stop_reason"] == "model_ended_without_submission", result
        assert not (sample / "accepted_answer.txt").exists()
        transcript = json.loads((sample / "messages.json").read_text())
        assert sum(m["role"] == "user" for m in transcript) == 1
        print("Submission gate: plain final text ends unaccepted without simulated user feedback; PASS")


if __name__ == "__main__":
    main()
