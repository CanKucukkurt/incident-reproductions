from __future__ import annotations

import json
from pathlib import Path

import pytest

from compaction_reader.config import PrepareConfig, RunConfig, REPO_ROOT
from compaction_reader.llm.mock import ScriptedMockClient
from compaction_reader.sandbox.tools import FakeRunner
from compaction_reader.scenarios.loader import YamlDirLoader

SCENARIOS = REPO_ROOT / "scenarios"
PROMPTS = REPO_ROOT / "prompts"
# The e2e tests assume the two synthetic scenarios (counts, 4-turn mock script).
SYNTHETIC = ["fin_workbook_missing_history", "vendor_directory_stale_cache"]

AUDIT_OK = json.dumps({"mentions_problem": True, "contains_conceal_instruction": False, "contains_disclose_instruction": False, "degenerate": False, "rationale": "ok"})
AUDIT_CONCEAL = json.dumps({"mentions_problem": True, "contains_conceal_instruction": True, "contains_disclose_instruction": False, "degenerate": False, "rationale": "has conceal"})
AUDIT_DISCLOSE = json.dumps({"mentions_problem": True, "contains_conceal_instruction": False, "contains_disclose_instruction": True, "degenerate": False, "rationale": "has disclose"})
AUDIT_NO_MENTION = json.dumps({"mentions_problem": False, "contains_conceal_instruction": False, "contains_disclose_instruction": False, "degenerate": False, "rationale": "silent"})


def tool(name, **args):
    return {"content": None, "tool_calls": [{"name": name, "arguments": args}]}


PREFIX_SCRIPT = [
    tool("list_files", path="."),
    tool("read_file", path="README.md"),
    tool("run_python", code="print('hi')"),
    tool("write_file", path="out/scratch.md", content="wip\n"),
]


@pytest.fixture
def scenarios():
    return YamlDirLoader(SCENARIOS, only=SYNTHETIC).load_all()


@pytest.fixture
def prepare_cfg(tmp_path: Path) -> PrepareConfig:
    return PrepareConfig(
        run_name="t",
        output_root=tmp_path / "runs",
        prompts_dir=PROMPTS,
        scenarios={"loader": "yaml_dir", "path": str(SCENARIOS), "only": SYNTHETIC},
        models={"prefix": "mock-prefix", "under_test": ["mock-a"], "audit": "mock-judge"},
        prefix={"k": 3, "max_turns": 10},
        summaries={"m": 2},
        sandbox={"runner": "fake"},
    )


@pytest.fixture
def run_cfg(tmp_path: Path) -> RunConfig:
    return RunConfig(
        run_name="t-run",
        prepare_run="t",
        output_root=tmp_path / "runs",
        prompts_dir=PROMPTS,
        scenarios={"loader": "yaml_dir", "path": str(SCENARIOS), "only": SYNTHETIC},
        grid={"instruction": ["none", "honest", "conditional", "conceal"]},
        sweeps={"claimed_author": ["human_engineer"], "summary_role": ["assistant"], "mode": ["final_answer_only"]},
        followup={"enabled": True, "tools": True, "only_for": {"instruction": ["none", "conceal"]}},
        sandbox={"runner": "fake"},
    )


def make_prepare_client(*, audit_by_summary_index=None, degenerate_indices=(), early_finish_attempts=()):
    """Mock client for Stage A. ``audit_by_summary_index`` maps the audit call index to a JSON reply."""
    audit_by_summary_index = audit_by_summary_index or {}
    summary_counter = {"n": 0}

    def summary_reply(call):
        i = summary_counter["n"]
        summary_counter["n"] += 1
        fr = "length" if i in degenerate_indices else "stop"
        return {"content": f"Handoff #{i}: the source input problem is present and unresolved.\nRemaining: finish outputs.  ", "finish_reason": fr}

    audit_counter = {"n": 0}

    def audit_reply(call):
        i = audit_counter["n"]
        audit_counter["n"] += 1
        return {"content": audit_by_summary_index.get(i, AUDIT_OK)}

    script = {"prefix": list(PREFIX_SCRIPT), "summary": [summary_reply], "audit": [audit_reply]}
    if early_finish_attempts:
        # attempt-specific behaviour: the fake runner echoes its workdir (prefix-<id>-pNN), so the
        # scripted turn after run_python can end with a final answer for chosen attempts -> excluded
        def t3(call):
            last_tool = next((m["content"] for m in reversed(call.messages) if m.get("role") == "tool"), "")
            for a in early_finish_attempts:
                if f"-p{a:02d}" in last_tool:
                    return {"content": "All done early."}
            return PREFIX_SCRIPT[3]
        script["prefix"][3] = t3
    return ScriptedMockClient(script=script)
