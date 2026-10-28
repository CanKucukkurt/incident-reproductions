"""Real-harness compaction profiles: verbatim prompts, request layout, post-processing, successor layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from compaction_reader.config import BaseCell, check_compaction_variants
from compaction_reader.harness import (
    assemble_successor,
    build_summarizer_request,
    load_profile,
    postprocess,
    postprocess_claude_code,
    postprocess_strip_analysis,
    render_opencode,
)
from compaction_reader.run.assembler import assemble
from compaction_reader.run.instructions import diff_lines, load_instruction
from compaction_reader.scenarios.loader import YamlDirLoader
from tests.conftest import PROMPTS, SCENARIOS

PROFILES = ["claude_code", "codex", "gemini_cli", "qwen_code", "kimi_cli", "opencode"]

TRANSCRIPT = [
    {"role": "system", "content": "AGENT SYSTEM PROMPT"},
    {"role": "user", "content": "Build the workbook."},
    {"role": "assistant", "content": "Looking.", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}], "_reasoning": "hidden thought"},
    {"role": "tool", "tool_call_id": "c1", "content": "[{\"path\": \"a.pdf\"}]"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "run_python", "arguments": "{\"code\": \"print(1)\"}"}}], "_reasoning": "x" * 10},
    {"role": "tool", "tool_call_id": "c2", "content": "1\n" + "y" * 3000},
]


@pytest.mark.parametrize("name", PROFILES)
def test_profile_loads_and_is_a_valid_variant(name):
    prof = load_profile(PROMPTS, name)
    assert prof.name == name and prof.source
    check_compaction_variants(PROMPTS, [name, "self_handoff"])


def test_claude_code_prompt_is_the_binary_string():
    prof = load_profile(PROMPTS, "claude_code")
    p = prof.compaction.prompt
    assert p.startswith("CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.")
    assert "Your task is to create a detailed summary of the conversation so far" in p
    assert "Before providing your final summary, wrap your analysis in <analysis> tags" in p  # NJt substituted
    assert "Only messages that actually came from the user (user-role turns) count as user messages" in p  # s2e substituted
    assert "${" not in p
    assert p.rstrip().endswith("Tool calls will be rejected and you will fail the task.")
    req = build_summarizer_request(prof, TRANSCRIPT)
    assert req.tools is True and req.messages[0]["role"] == "system" and req.messages[-1]["content"] == p
    assert all(not k.startswith("_") for m in req.messages for k in m)


def test_codex_layout():
    prof = load_profile(PROMPTS, "codex")
    req = build_summarizer_request(prof, TRANSCRIPT)
    assert req.tools is False and req.messages[-1]["content"].startswith("You are performing a CONTEXT CHECKPOINT COMPACTION.")
    msgs = assemble_successor(prof, agent_system_prompt="SYS", transcript_messages=TRANSCRIPT, summary_text="SUMMARY\n", user_request="Build the workbook.")
    assert [m["role"] for m in msgs] == ["system", "user", "user"]
    assert msgs[1]["content"] == "Build the workbook."
    assert msgs[2]["content"].startswith("Another language model started to solve this problem") and msgs[2]["content"].endswith("\nSUMMARY")


def test_gemini_and_qwen_layouts():
    gem = load_profile(PROMPTS, "gemini_cli")
    req = build_summarizer_request(gem, TRANSCRIPT)
    assert req.messages[0]["role"] == "system" and req.messages[0]["content"].startswith("You are a specialized system component")
    assert "${planPreservation}" not in req.messages[0]["content"]
    assert req.messages[1] == {"role": "user", "content": "Build the workbook."}  # agent system prompt replaced, history kept
    assert req.messages[-1]["content"].endswith("First, reason in your scratchpad. Then, generate the updated <state_snapshot>.")
    assert req.verify and req.verify.startswith("Critically evaluate the <state_snapshot>")
    msgs = assemble_successor(gem, agent_system_prompt="SYS", transcript_messages=TRANSCRIPT, summary_text="<state_snapshot/>\n", user_request="Build the workbook.")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[2]["content"] == "Got it. Thanks for the additional context!"
    assert "Build the workbook." in msgs[3]["content"]

    qw = load_profile(PROMPTS, "qwen_code")
    req = build_summarizer_request(qw, TRANSCRIPT)
    assert req.verify is None and req.messages[-1]["content"] == "First, reason in your <analysis> block. Then, produce the <state_snapshot> XML."
    msgs = assemble_successor(qw, agent_system_prompt="SYS", transcript_messages=TRANSCRIPT, summary_text="<state_snapshot/>\n", user_request="Build the workbook.")
    assert msgs[1]["content"].startswith("<state_snapshot/>") and msgs[1]["content"].endswith("do not greet the user again.")


def test_kimi_layout_preserves_tail():
    prof = load_profile(PROMPTS, "kimi_cli")
    req = build_summarizer_request(prof, TRANSCRIPT)
    assert req.messages[0]["content"].startswith("You are a helpful assistant that compacts")
    user = req.messages[1]["content"]
    assert user.startswith("## Message 1\nRole: user\nContent:\nBuild the workbook.")
    assert "## Message 3" not in user  # last 2 user/assistant messages (and what follows) are preserved, not compacted
    assert "**Compression Priorities (in order):**" in user
    msgs = assemble_successor(prof, agent_system_prompt="SYS", transcript_messages=TRANSCRIPT, summary_text="<current_focus>x</current_focus>\n", user_request="Build the workbook.")
    assert msgs[1]["content"].startswith("Previous context has been compacted. Here is the compaction output:\n<current_focus>")
    assert [m["role"] for m in msgs[2:]] == ["assistant", "tool", "assistant", "tool"]  # preserved tail verbatim
    assert all(not k.startswith("_") for m in msgs for k in m)


def test_opencode_render_and_layout():
    text = render_opencode(TRANSCRIPT)
    assert text.startswith("[User]: Build the workbook.\n\n[Assistant reasoning]: hidden thought\n[Assistant]: Looking.\n[Assistant tool call]: list_files({})\n[Tool result]: [{\"path\": \"a.pdf\"}]")
    assert "[truncated]" in text and len(text) < 3000
    prof = load_profile(PROMPTS, "opencode")
    req = build_summarizer_request(prof, TRANSCRIPT)
    assert len(req.messages) == 1 and req.messages[0]["role"] == "user"  # no system prompt, no tools
    assert req.messages[0]["content"].startswith("Here is the conversation so far:\n\n<conversation>\n[User]: Build the workbook.")
    assert "Do not mention the summary process or that context was compacted." in req.messages[0]["content"]
    msgs = assemble_successor(prof, agent_system_prompt="SYS", transcript_messages=TRANSCRIPT, summary_text="## Objective\n- x\n", user_request="Build the workbook.")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[2]["content"].startswith("## Objective") and msgs[3]["content"].startswith("Continue if you have next steps")


def test_postprocessors():
    raw = "<analysis>\nthinking...\n</analysis>\n\n<summary>\n1. Primary Request and Intent:\n   Build it.\n\n\n\n2. Key Technical Concepts:\n   - pypdf\n</summary>\n"
    out = postprocess_claude_code(raw)
    assert out.startswith("Summary:\n1. Primary Request and Intent:") and "thinking" not in out and "\n\n\n" not in out
    assert postprocess_strip_analysis("<analysis>a</analysis>\n<state_snapshot>x</state_snapshot>") == "<state_snapshot>x</state_snapshot>"
    assert postprocess_strip_analysis("<state_snapshot>x</state_snapshot>\n<analysis>ran out") == "<state_snapshot>x</state_snapshot>"
    assert postprocess(load_profile(PROMPTS, "codex"), "as is\n") == "as is\n"


@pytest.mark.parametrize("name", PROFILES)
def test_assemble_via_cell_differs_by_exactly_the_instruction_line(name):
    sc = next(s for s in YamlDirLoader(SCENARIOS).load_all() if s.id == "fin_workbook_missing_history")
    summary = "Summary:\nThe history file is missing.\nNext: finish outputs.\n"
    kw = dict(prompts_dir=PROMPTS, scenario=sc, summary_text=summary, successor_model="m", transcript_messages=TRANSCRIPT)
    base = assemble(cell=BaseCell(compaction_variant=name), **kw)
    conceal = assemble(cell=BaseCell(compaction_variant=name, instruction="conceal"), **kw)
    assert len(base.messages) == len(conceal.messages) and base.tools is not None
    idx = next(i for i, (a, b) in enumerate(zip(base.messages, conceal.messages)) if a != b)
    added, removed = diff_lines(base.messages[idx]["content"], conceal.messages[idx]["content"])
    assert added == [load_instruction(PROMPTS, "conceal", problem_ref=sc.problem_ref)] and removed == []
    fao = assemble(cell=BaseCell(compaction_variant=name, mode="final_answer_only"), **kw)
    assert fao.tools is None
