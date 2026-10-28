"""Assemble the successor's context: agent system prompt, tool definitions,
handoff preamble + (edited) summary in the configured role, and the original
user request as the resume message."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import BaseCell
from ..prompts_io import read_prompt, render
from ..sandbox.tools import load_tool_specs
from ..scenarios.models import Scenario
from ..harness import assemble_successor, is_harness_variant, load_profile
from .instructions import insert_line, load_instruction, replace_plans


@dataclass
class Assembled:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    summary_text: str  # the summary as inserted (edited or not)
    instruction_line: str | None


def assemble(
    *,
    prompts_dir: Path,
    scenario: Scenario,
    summary_text: str,
    cell: BaseCell,
    successor_model: str,
    other_model_name: str = "a different model",
    transcript_messages: list[dict[str, Any]] | None = None,
    plan_sentences: list[dict[str, str]] | None = None,
) -> Assembled:
    line = load_instruction(prompts_dir, cell.instruction, problem_ref=scenario.problem_ref)
    if cell.edit_mode == "replace":
        edited = replace_plans(summary_text, line, plan_sentences or [])
    else:
        edited = insert_line(summary_text, line, cell.insert_position, anchor=scenario.problem_anchor)
    if is_harness_variant(prompts_dir, cell.compaction_variant):
        # Real-harness layout: claimed_author / summary_role do not apply (the profile fixes both).
        profile = load_profile(prompts_dir, cell.compaction_variant)
        messages = assemble_successor(
            profile,
            agent_system_prompt=read_prompt(prompts_dir, "agent", "system.md").strip(),
            transcript_messages=transcript_messages or [],
            summary_text=edited,
            user_request=scenario.user_request,
        )
        tools = None if cell.mode == "final_answer_only" else load_tool_specs(prompts_dir)
        return Assembled(messages=messages, tools=tools, summary_text=edited, instruction_line=line)
    preamble = render(
        read_prompt(prompts_dir, "handoff", f"{cell.claimed_author}.md"),
        author_model=other_model_name,
        successor_model=successor_model,
    ).strip()
    resume = render(read_prompt(prompts_dir, "handoff", "resume_user.md"), user_request=scenario.user_request.strip()).strip()
    messages = [
        {"role": "system", "content": read_prompt(prompts_dir, "agent", "system.md").strip()},
        {"role": cell.summary_role, "content": preamble + "\n\n" + edited.rstrip("\n")},
        {"role": "user", "content": resume},
    ]
    tools = None if cell.mode == "final_answer_only" else load_tool_specs(prompts_dir)
    return Assembled(messages=messages, tools=tools, summary_text=edited, instruction_line=line)
