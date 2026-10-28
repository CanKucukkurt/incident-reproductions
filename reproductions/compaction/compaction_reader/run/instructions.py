"""Deterministic insertion of the single instruction line into a summary."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from ..errors import ConfigError
from ..prompts_io import read_prompt, render

PROBLEM_REF = "{problem_ref}"


def load_instruction(prompts_dir: Path, name: str, problem_ref: str | None = None) -> str | None:
    """The single instruction line. A ``{problem_ref}`` placeholder is replaced with the
    scenario's ``problem_ref`` (how the summaries themselves name the problem)."""
    if name == "none":
        return None
    text = read_prompt(prompts_dir, "instructions", f"{name}.txt").strip()
    if not text or "\n" in text:
        raise ConfigError(f"instruction '{name}' must be exactly one non-empty line")
    if PROBLEM_REF in text:
        if not problem_ref:
            raise ConfigError(f"instruction '{name}' uses {PROBLEM_REF} but the scenario has no problem_ref")
        text = render(text, problem_ref=problem_ref)
    return text


def insert_line(summary: str, line: str | None, position: str = "append", anchor: str | list[str] | None = None) -> str:
    """Insert ``line`` into ``summary``. ``summary`` is expected to end with one newline
    (see ``normalize_summary``). Positions: append | prepend | after_line:N (1-based) |
    after_match:<regex> | inline. ``after_match`` searches the whole summary text for the
    regex and puts the line right after the line in which the first match ends, with that
    line's indentation and bullet marker (so ``(?s)Errors.*?foo\\.pdf`` lands on the first mention of foo.pdf
    inside the Errors section). ``inline`` does the same with the scenario's
    ``problem_anchor``, a regex or a list of regexes tried in order. Both fail if nothing matches."""
    if line is None:
        return summary
    if not summary.endswith("\n"):
        summary += "\n"
    if position == "append":
        return summary + line + "\n"
    if position == "prepend":
        return line + "\n" + summary
    if position == "inline" or position.startswith("after_match:"):
        patterns = anchor if position == "inline" else position.split(":", 1)[1]
        if isinstance(patterns, str):
            patterns = [patterns]
        if not patterns:
            raise ConfigError("insert_position 'inline' needs the scenario's problem_anchor")
        for pattern in patterns:
            try:
                m = re.compile(pattern).search(summary)
            except re.error as e:
                raise ConfigError(f"bad insert_position regex {pattern!r}: {e}") from e
            if m:
                last = max(m.end() - 1, 0)  # index of the match's last char (may itself be a newline)
                line_end = summary.find("\n", last)  # end of the line the match ends in
                line_start = summary.rfind("\n", 0, line_end) + 1
                anchored = summary[line_start:line_end]
                indent = anchored[: len(anchored) - len(anchored.lstrip())]
                bullet = re.match(r"([-*•]\s+|\d+[.)]\s+)", anchored.lstrip())  # sibling bullet, not an orphan line
                prefix = indent + (bullet.group(1) if bullet else "")
                return summary[: line_end + 1] + prefix + line + "\n" + summary[line_end + 1 :]
        raise ConfigError(f"insert_position {position!r}: no summary line matches {patterns!r}")
    if position.startswith("after_line:"):
        try:
            n = int(position.split(":", 1)[1])
        except ValueError as e:
            raise ConfigError(f"bad insert_position: {position}") from e
        lines = summary.splitlines(keepends=True)
        n = max(0, min(n, len(lines)))
        return "".join(lines[:n]) + line + "\n" + "".join(lines[n:])
    raise ConfigError(f"unknown insert_position: {position}")


def _tidy_sentence(rest: str) -> str:
    """Tidy a sentence after its disclosure clause was cut out: drop a dangling connector /
    comma / semicolon before the end and keep (or restore) the terminal punctuation."""
    m = re.match(r"^(.*?)([.!?]?)\s*$", rest, re.S)
    body, end = (m.group(1), m.group(2)) if m else (rest, "")
    body = re.sub(r"[\s,;:]+(?:and|or|but|also|while)?\s*$", "", body)
    body = re.sub(r"[\s,;:]+$", "", body)
    if not end and re.search(r"[.!?][\"'\u201d\u2019)\]]*$", body):
        return body  # already ends in terminal punctuation followed by a closing quote/bracket
    return body + (end or ".")


def replace_plans(summary: str, line: str | None, plans: list[dict[str, str]]) -> str:
    """``edit_mode: replace``. ``plans`` are the summary's own located plans about telling the
    user, each {sentence, clause} verbatim (see ``prepare.locate``). The clause is cut out of
    every plan sentence; the instruction line is placed right after the first (trimmed)
    sentence. A sentence whose clause is the whole sentence is replaced outright (first) or
    removed (rest); a summary line left with only its bullet marker is dropped."""
    if line is None:
        return summary
    if not plans:
        raise ConfigError("edit_mode 'replace' needs at least one located plan sentence")
    out = summary
    for i, pl in enumerate(plans):
        sent, clause = pl["sentence"], pl.get("clause") or pl["sentence"]
        if sent not in out:
            raise ConfigError(f"plan sentence not found verbatim in summary: {sent[:80]!r}")
        if clause not in sent:
            raise ConfigError(f"plan clause is not inside its sentence: {clause[:80]!r}")
        whole = clause.strip() == sent.strip()
        if whole:
            new = line if i == 0 else ""
        else:
            new = _tidy_sentence(sent.replace(clause, "", 1)) + (" " + line if i == 0 else "")
        if new:
            out = out.replace(sent, new, 1)
        else:  # removal: also eat one adjacent space so no double space is left behind
            out = re.sub(re.escape(sent) + r" ?", "", out, count=1)
    original_lines = set(summary.splitlines())
    cleaned = []
    for ln in out.splitlines(keepends=True):
        if ln.strip() and re.fullmatch(r"\s*(?:[-*•]|\d+[.)])\s*", ln):
            continue  # only a bullet marker survived
        if ln.rstrip("\n") not in original_lines:  # a line we edited: tidy the seams, leave untouched lines byte-identical
            nl = "\n" if ln.endswith("\n") else ""
            body = ln.rstrip("\n")
            body = re.sub(r"(?<=\S)  +(?=\S)", " ", body).rstrip()
            ln = body + nl
        cleaned.append(ln)
    return "".join(cleaned)


def summary_diff(a: str, b: str, a_name: str = "a", b_name: str = "b") -> str:
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True), fromfile=a_name, tofile=b_name))


def diff_lines(a: str, b: str) -> tuple[list[str], list[str]]:
    """(added, removed) lines between two texts, ignoring diff headers."""
    added, removed = [], []
    for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
        if ln.startswith("+++") or ln.startswith("---") or ln.startswith("@@"):
            continue
        if ln.startswith("+"):
            added.append(ln[1:])
        elif ln.startswith("-"):
            removed.append(ln[1:])
    return added, removed
