"""Real agent-harness compaction profiles.

A profile under ``prompts/harness/<name>/profile.yaml`` replicates how one real
harness (Claude Code, Codex, Gemini CLI, Qwen Code, Kimi CLI, OpenCode) elicits
a compaction summary and how it re-injects that summary for the successor. The
prompt texts next to it are verbatim copies from the harness sources (see each
profile's ``source`` field). A profile is selected by using its name as a
``compaction_variant``; the generic ``prompts/compaction/<name>.md`` variants
keep working unchanged.

Summariser request modes
  append_user          the compaction prompt is appended as a user message to the
                       live transcript (agent system prompt kept)      Claude Code, Codex
  system_over_history  the summariser's own system prompt replaces the agent's;
                       the history is passed as-is, then a kickoff user turn   Gemini CLI, Qwen Code
  rendered_user        the history is rendered to text inside one user message
                       (optionally with a summariser system prompt)      Kimi CLI, OpenCode
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .prompts_io import read_prompt

OPENCODE_TOOL_OUTPUT_MAX_CHARS = 2_000


@dataclass
class CompactionSpec:
    mode: str
    prompt: str | None = None
    system: str | None = None
    keep_agent_system: bool = True
    tools: str = "none"  # keep | none
    kickoff: str | None = None
    verify: str | None = None
    render: str | None = None  # kimi | opencode
    preserve_tail: int = 0
    postprocess: str = "none"  # claude_code | strip_analysis | none


@dataclass
class HandoffSpec:
    role: str = "user"
    preamble: str = ""
    suffix: str = ""
    keep_user_messages: bool = False
    ack: str | None = None
    preserve_tail: bool = False
    resume: str | None = None


@dataclass
class HarnessProfile:
    name: str
    source: str
    compaction: CompactionSpec
    handoff: HandoffSpec
    dir: Path
    raw: dict[str, Any] = field(default_factory=dict)


def profile_path(prompts_dir: Path, name: str) -> Path:
    return Path(prompts_dir) / "harness" / name / "profile.yaml"


def is_harness_variant(prompts_dir: Path, name: str) -> bool:
    return profile_path(prompts_dir, name).is_file()


def load_profile(prompts_dir: Path, name: str) -> HarnessProfile:
    p = profile_path(prompts_dir, name)
    if not p.is_file():
        raise ConfigError(f"harness profile not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    d = p.parent

    def local(key: str, section: dict[str, Any]) -> str | None:
        f = section.get(key)
        return read_prompt(d, f) if f else None

    c = raw.get("compaction") or {}
    h = raw.get("handoff") or {}
    comp = CompactionSpec(
        mode=c.get("mode", "append_user"),
        prompt=local("prompt_file", c),
        system=local("system_file", c),
        keep_agent_system=bool(c.get("keep_agent_system", True)),
        tools=c.get("tools", "none"),
        kickoff=local("kickoff_file", c),
        verify=local("verify_file", c),
        render=c.get("render"),
        preserve_tail=int(c.get("preserve_tail", 0) or 0),
        postprocess=c.get("postprocess", "none"),
    )
    if comp.mode not in ("append_user", "system_over_history", "rendered_user"):
        raise ConfigError(f"{p}: unknown compaction.mode {comp.mode!r}")
    if comp.mode in ("append_user", "rendered_user") and comp.prompt is None:
        raise ConfigError(f"{p}: compaction.prompt_file is required for mode {comp.mode}")
    if comp.mode == "system_over_history" and comp.system is None:
        raise ConfigError(f"{p}: compaction.system_file is required for mode system_over_history")
    resume = h.get("resume")
    resume_text = None if resume in (None, "none") else read_prompt(prompts_dir, resume)
    hand = HandoffSpec(
        role=h.get("role", "user"),
        preamble=local("preamble_file", h) or "",
        suffix=local("suffix_file", h) or "",
        keep_user_messages=bool(h.get("keep_user_messages", False)),
        ack=h.get("ack"),
        preserve_tail=bool(h.get("preserve_tail", False)),
        resume=resume_text,
    )
    if hand.role not in ("user", "assistant"):
        raise ConfigError(f"{p}: handoff.role must be user or assistant")
    return HarnessProfile(name=raw.get("name", name), source=raw.get("source", ""), compaction=comp, handoff=hand, dir=d, raw=raw)


# --------------------------------------------------------------------------
# History helpers
# --------------------------------------------------------------------------


def _public(m: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in m.items() if not k.startswith("_")}


def _split_tail(messages: list[dict[str, Any]], n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Kimi CLI's rule: keep the last ``n`` user/assistant-role messages (and everything after
    the first of them) out of the compaction input."""
    if n <= 0:
        return list(messages), []
    count = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") in ("user", "assistant"):
            count += 1
            if count == n:
                return list(messages[:i]), list(messages[i:])
    return [], list(messages)


def _tool_call_view(tc: dict[str, Any]) -> tuple[str, Any]:
    fn = tc.get("function", {})
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    return fn.get("name", ""), args


def render_kimi(messages: list[dict[str, Any]]) -> str:
    """kimi-cli SimpleCompaction.prepare: '## Message N\\nRole: r\\nContent:\\n' + text parts.
    Only text content is rendered (tool-call arguments are not text parts)."""
    out = []
    for i, m in enumerate(messages):
        out.append(f"## Message {i + 1}\nRole: {m.get('role')}\nContent:\n")
        if m.get("content"):
            out.append(str(m["content"]))
    return "".join(out)


def render_opencode(messages: list[dict[str, Any]]) -> str:
    """opencode serialize(): [User]/[Assistant]/[Assistant reasoning]/[Assistant tool call]/[Tool result]
    lines, tool output truncated to 2000 chars, messages joined by blank lines."""
    def trunc(s: str) -> str:
        return s if len(s) <= OPENCODE_TOOL_OUTPUT_MAX_CHARS else s[:OPENCODE_TOOL_OUTPUT_MAX_CHARS] + "\n[truncated]"

    results: dict[str, str] = {m.get("tool_call_id", ""): m.get("content") or "" for m in messages if m.get("role") == "tool"}
    blocks = []
    for m in messages:
        role = m.get("role")
        if role == "system" or role == "tool":
            continue  # tool results are rendered with their call; the agent system prompt is not part of the parts
        if role == "user":
            if m.get("content"):
                blocks.append(f"[User]: {m['content']}")
            continue
        lines = []
        if m.get("_reasoning"):
            lines.append(f"[Assistant reasoning]: {m['_reasoning']}")
        if m.get("content"):
            lines.append(f"[Assistant]: {m['content']}")
        for tc in m.get("tool_calls") or []:
            name, args = _tool_call_view(tc)
            lines.append(f"[Assistant tool call]: {name}({json.dumps(args, separators=(',', ':'))})")
            if tc.get("id") in results:
                lines.append(f"[Tool result]: {trunc(results[tc['id']])}")
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(b for b in blocks if b)


# --------------------------------------------------------------------------
# Summariser request
# --------------------------------------------------------------------------


@dataclass
class SummarizerRequest:
    messages: list[dict[str, Any]]
    tools: bool  # True: pass the agent's tool specs
    verify: str | None  # second-pass user message, if the harness does one


def build_summarizer_request(profile: HarnessProfile, transcript_messages: list[dict[str, Any]]) -> SummarizerRequest:
    c = profile.compaction
    history = [_public(m) for m in transcript_messages]
    if c.mode == "append_user":
        msgs = history if c.keep_agent_system else [m for m in history if m.get("role") != "system"]
        msgs = msgs + [{"role": "user", "content": c.prompt or ""}]
        return SummarizerRequest(messages=msgs, tools=(c.tools == "keep"), verify=c.verify)
    if c.mode == "system_over_history":
        body = [m for m in history if m.get("role") != "system"]
        msgs = [{"role": "system", "content": c.system or ""}] + body
        if c.kickoff:
            msgs.append({"role": "user", "content": c.kickoff.strip()})
        return SummarizerRequest(messages=msgs, tools=False, verify=c.verify)
    # rendered_user
    body = [m for m in transcript_messages if m.get("role") != "system"]
    to_compact, _tail = _split_tail(body, c.preserve_tail)
    if c.render == "kimi":
        user = render_kimi(to_compact) + "\n" + (c.prompt or "")
    elif c.render == "opencode":
        user = (c.prompt or "").replace("{conversation}", render_opencode(to_compact))
    else:
        raise ConfigError(f"harness {profile.name}: rendered_user needs render: kimi|opencode")
    msgs: list[dict[str, Any]] = []
    if c.system:
        msgs.append({"role": "system", "content": c.system})
    msgs.append({"role": "user", "content": user})
    return SummarizerRequest(messages=msgs, tools=False, verify=c.verify)


# --------------------------------------------------------------------------
# Post-processing of the raw summary
# --------------------------------------------------------------------------

_ANALYSIS = re.compile(r"<analysis>[\s\S]*?</analysis>\s*")
_ANALYSIS_OPEN = re.compile(r"<analysis>[\s\S]*$")
_SUMMARY = re.compile(r"<summary>([\s\S]*?)</summary>")


def postprocess_claude_code(text: str) -> str:
    """Claude Code (function Duo): drop <analysis>, unwrap <summary> as 'Summary:\\n...', squeeze blank lines."""
    n = re.sub(r"<analysis>[\s\S]*?</analysis>", "", text, count=1)
    n = _SUMMARY.sub(lambda m: "Summary:\n" + m.group(1).strip(), n, count=1)
    n = re.sub(r"\n\n+", "\n\n", n)
    return n.strip()


def postprocess_strip_analysis(text: str) -> str:
    """Qwen Code stripAnalysisBlock: closed blocks, then an unclosed trailing one."""
    n = _ANALYSIS.sub("", text)
    n = _ANALYSIS_OPEN.sub("", n)
    return n.strip()


def postprocess(profile: HarnessProfile, text: str) -> str:
    kind = profile.compaction.postprocess
    if kind == "claude_code":
        return postprocess_claude_code(text)
    if kind == "strip_analysis":
        return postprocess_strip_analysis(text)
    if kind in ("none", None):
        return text
    raise ConfigError(f"harness {profile.name}: unknown postprocess {kind!r}")


# --------------------------------------------------------------------------
# Successor context
# --------------------------------------------------------------------------


def assemble_successor(
    profile: HarnessProfile,
    *,
    agent_system_prompt: str,
    transcript_messages: list[dict[str, Any]],
    summary_text: str,
    user_request: str,
) -> list[dict[str, Any]]:
    """Messages the successor sees, exactly as the harness would lay them out:
    [system] (+ original user messages) + summary message (+ ack) (+ preserved tail) (+ resume)."""
    h = profile.handoff
    msgs: list[dict[str, Any]] = [{"role": "system", "content": agent_system_prompt}]
    if h.keep_user_messages:
        msgs.append({"role": "user", "content": user_request.strip()})
    body = (h.preamble + summary_text.rstrip("\n") + h.suffix.rstrip("\n")).rstrip("\n")
    msgs.append({"role": h.role, "content": body})
    if h.ack:
        msgs.append({"role": "assistant", "content": h.ack})
    if h.preserve_tail:
        _, tail = _split_tail([m for m in transcript_messages if m.get("role") != "system"], profile.compaction.preserve_tail)
        msgs.extend(_public(m) for m in tail)
    if h.resume:
        msgs.append({"role": "user", "content": h.resume.replace("{user_request}", user_request.strip()).strip()})
    return msgs
