"""Transcript surgery with real re-execution.

Apply an edit spec to a FULL trajectory (runs/<run>/prefixes_full/<scenario>/<id>): drop turns,
rewrite tool-call arguments (exact or regex substitutions), rewrite the final answer, blank stored
reasoning that matches a regex. Every turn from `reexecute_from` (source numbering) onward is then
RE-EXECUTED in the Docker sandbox from the snapshot preceding it, so tool results, per-turn
snapshots and state checks are real, not authored. The result is written as a new full trajectory
under the config's run dir (same format as `cr prepare`, plus an `edit` block with every original
string), and the config's cut rules are materialised so `cr prepare --stage summaries` works.

    .venv/bin/python scripts/edit_trajectory.py -c configs/prepare.guided-sol-stripped.yaml --spec configs/edits/sol-fab-strip.json

Spec (JSON):
  source: {run, scenario, prefix_id}      new_id: str
  drop_turns: [int, ...]                  (source turn numbers)
  reexecute_from: int                     (source turn number; every kept turn >= this is re-run)
  code_edits: [{turn, call, find | regex, replace, count?}]   applied to the tool call's JSON arguments
                                          (`code` for run_python, `content` for write_file); every
                                          find/regex must match exactly `count` times (default 1)
  text_edits: [{turn, set | find | regex, replace}]   assistant TEXT of that turn (source numbering); `set` replaces it whole
  final_answer_edits: [{find | regex, replace}]
  blank_reasoning_regex: str              stored reasoning summaries matching it are blanked (never sent to the API anyway)
Each edit must match, otherwise the script stops before touching the sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import difflib
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compaction_reader.cli import make_runner  # noqa: E402
from compaction_reader.config import load_prepare_config  # noqa: E402
from compaction_reader.prepare.prefix import _FullResult, _materialize_cuts, full_dir, prefixes_dir  # noqa: E402
from compaction_reader.sandbox.snapshot import restore_snapshot, take_snapshot  # noqa: E402
from compaction_reader.sandbox.tools import ToolRegistry, load_tool_specs  # noqa: E402
from compaction_reader.scenarios.loader import get_loader  # noqa: E402

log = logging.getLogger("edit")


def turn_segments(full: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """messages grouped per turn (assistant message + its tool results); index 0 = turn 1."""
    msgs, ends = full["messages"], full["turn_ends"]
    segs, start = [], 2
    for end in ends:
        segs.append([copy.deepcopy(m) for m in msgs[start:end]])
        start = end
    return segs


def apply_sub(text: str, edit: dict[str, Any], where: str) -> str:
    want = int(edit.get("count", 1))
    if "regex" in edit:
        rx = re.compile(edit["regex"], re.S)
        n = len(rx.findall(text))
        if n != want:
            raise SystemExit(f"{where}: regex {edit['regex']!r} matched {n} times, expected {want}")
        return rx.sub(lambda m: edit["replace"], text)
    n = text.count(edit["find"])
    if n != want:
        raise SystemExit(f"{where}: find string matched {n} times, expected {want}: {edit['find'][:80]!r}")
    return text.replace(edit["find"], edit["replace"])


async def main_async(a: argparse.Namespace) -> int:
    cfg = load_prepare_config(a.config)
    spec = json.loads(Path(a.spec).read_text())
    src = spec["source"]
    src_dir = Path(cfg.output_root) / src["run"] / "prefixes_full" / src["scenario"] / src["prefix_id"]
    full = json.loads((src_dir / "transcript.json").read_text())
    scenario = {s.id: s for s in get_loader({**cfg.scenarios, "only": None}).load_all()}[spec.get("scenario", src["scenario"])]
    new_id = spec["new_id"]
    segs = turn_segments(full)
    n_src = len(segs)
    drop = set(spec.get("drop_turns", []))
    reexec_from = int(spec["reexecute_from"])
    edit_log: dict[str, Any] = {"spec": spec, "source_dir": str(src_dir), "code_edits": [], "final_answer_edits": [], "reasoning_blanked": [], "dropped_turns": sorted(drop)}

    # 1. code edits (source numbering), all validated before anything runs
    for e in spec.get("code_edits", []):
        t, k = int(e["turn"]), int(e.get("call", 0))
        if t in drop:
            raise SystemExit(f"edit targets dropped turn {t}")
        asst = next(m for m in segs[t - 1] if m["role"] == "assistant")
        tc = asst["tool_calls"][k]
        args = json.loads(tc["function"]["arguments"])
        key = "code" if tc["function"]["name"] == "run_python" else "content"
        old = args[key]
        args[key] = apply_sub(old, e, f"turn {t} call {k}")
        tc["function"]["arguments"] = json.dumps(args)
        edit_log["code_edits"].append({"turn": t, "call": k, "diff": "".join(difflib.unified_diff(old.splitlines(True), args[key].splitlines(True), "before", "after", n=1))})
    # 1b. assistant text edits
    for e in spec.get("text_edits", []):
        t = int(e["turn"])
        if t in drop:
            raise SystemExit(f"text edit targets dropped turn {t}")
        asst = next(m for m in segs[t - 1] if m["role"] == "assistant")
        old = asst.get("content") or ""
        asst["content"] = e["set"] if "set" in e else apply_sub(old, e, f"turn {t} text")
        edit_log.setdefault("text_edits", []).append({"turn": t, "before": old, "after": asst["content"]})
    # 2. final answer edits
    last = segs[-1]
    if spec.get("final_answer_edits"):
        asst = next(m for m in last if m["role"] == "assistant")
        old = asst.get("content") or ""
        new = old
        for e in spec["final_answer_edits"]:
            new = apply_sub(new, e, "final answer")
        asst["content"] = new
        edit_log["final_answer_edits"].append({"before": old, "after": new})
    # 3. reasoning blanking
    rx_r = re.compile(spec["blank_reasoning_regex"], re.I | re.S) if spec.get("blank_reasoning_regex") else None
    reasoning = list(full["reasoning"])
    for t in range(1, n_src + 1):
        r = reasoning[t - 1]
        if rx_r and r and rx_r.search(r):
            edit_log["reasoning_blanked"].append({"turn": t, "original": r})
            reasoning[t - 1] = None
            for m in segs[t - 1]:
                if m["role"] == "assistant":
                    m["_reasoning"] = None

    # 4. kept turns, new numbering
    kept_src_turns = [t for t in range(1, n_src + 1) if t not in drop]
    if any(t < reexec_from for t in drop):
        raise SystemExit("dropped turns must be >= reexecute_from (snapshots before the re-execution point are copied as-is)")
    fdir = full_dir(cfg.run_dir, scenario.id) / new_id
    if fdir.exists():
        if not a.overwrite:
            raise SystemExit(f"{fdir} exists; pass --overwrite")
        shutil.rmtree(fdir)
    snaps = fdir / "snapshots"
    snaps.mkdir(parents=True)
    sandbox = cfg.tmp_root / f"edit-{new_id}"
    # copy snapshots of untouched turns, restore the one before the re-execution point
    new_t = 0
    for t in kept_src_turns:
        if t >= reexec_from:
            break
        new_t += 1
        shutil.copytree(src_dir / "snapshots" / f"turn_{t:02d}", snaps / f"turn_{new_t:02d}")
    restore_snapshot(src_dir / "snapshots" / f"turn_{reexec_from - 1:02d}", sandbox)
    runner = make_runner(cfg.sandbox, None)
    tools = ToolRegistry(sandbox, runner=runner, specs=load_tool_specs(cfg.prompts_dir))

    # 5. rebuild messages, re-executing from reexec_from
    messages: list[dict[str, Any]] = [copy.deepcopy(full["messages"][0]), copy.deepcopy(full["messages"][1])]
    turn_ends: list[int] = []
    tool_events: list[dict[str, Any]] = []
    new_reasoning: list[str | None] = []
    upt: list[dict[str, int]] = []
    result_diffs: list[dict[str, Any]] = []
    new_t = 0
    for t in kept_src_turns:
        new_t += 1
        seg = segs[t - 1]
        asst = next(m for m in seg if m["role"] == "assistant")
        old_results = {m["tool_call_id"]: m["content"] for m in seg if m["role"] == "tool"}
        messages.append(asst)
        new_reasoning.append(reasoning[t - 1])
        upt.append(full["usage_per_turn"][t - 1])
        for tc in asst.get("tool_calls") or []:
            args = json.loads(tc["function"]["arguments"])
            if t >= reexec_from:
                res = await tools.execute(tc["function"]["name"], args)
                if res != old_results.get(tc["id"]):
                    result_diffs.append({"turn_src": t, "turn_new": new_t, "call_id": tc["id"], "before": old_results.get(tc["id"], "")[:600], "after": res[:600]})
            else:
                res = old_results[tc["id"]]
            tool_events.append({"turn": new_t, "id": tc["id"], "name": tc["function"]["name"], "arguments": args, "result": res})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
        turn_ends.append(len(messages))
        if t >= reexec_from and (asst.get("tool_calls") or []):
            take_snapshot(sandbox, snaps / f"turn_{new_t:02d}")
        elif t >= reexec_from:  # final-answer turn: state after it = final sandbox
            pass
    total_turns = new_t
    stopped_by = full["stopped_by"]
    final_answer = next(m for m in segs[kept_src_turns[-1] - 1] if m["role"] == "assistant").get("content") if stopped_by == "final_answer" else None
    n_candidates = total_turns - 1 if stopped_by == "final_answer" else total_turns
    checks = [scenario.run_state_check(snaps / f"turn_{t:02d}" / "files", messages[: turn_ends[t - 1]]).model_dump() for t in range(1, n_candidates + 1)]
    final_check = scenario.run_state_check(sandbox, messages).model_dump()
    take_snapshot(sandbox, fdir / "snapshot_final")
    edit_log["result_diffs"] = result_diffs
    rec = {
        "prefix_id": new_id, "scenario_id": scenario.id, "model": full["model"], "messages": messages, "tool_events": tool_events,
        "reasoning": new_reasoning, "usage": {k: sum(int(u.get(k, 0)) for u in upt) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "usage_per_turn": upt, "turns": total_turns, "turn_ends": turn_ends, "stopped_by": stopped_by, "finish_reason": full.get("finish_reason"),
        "final_answer": final_answer, "final_state_check": final_check, "state_check_by_turn": checks,
        "guide": full.get("guide"), "edit": {k: v for k, v in edit_log.items() if k != "spec"} | {"spec_path": str(a.spec)},
    }
    (fdir / "transcript.json").write_text(json.dumps(rec, indent=1))
    (fdir / "edit.json").write_text(json.dumps(edit_log, indent=1))
    if not cfg.sandbox.keep_sandboxes:
        shutil.rmtree(sandbox, ignore_errors=True)
    outcome, kept = _materialize_cuts(cfg, scenario, 0, new_id, fdir, _FullResult(rec))
    logp = prefixes_dir(cfg.run_dir, scenario.id) / "prefix_log.json"
    prev = json.loads(logp.read_text()) if logp.is_file() else {"attempts": 0, "kept": 0, "rows": []}
    prev["rows"] = [r for r in prev["rows"] if r["prefix_id"] != new_id] + [outcome]
    prev["attempts"] = len(prev["rows"]); prev["kept"] = sum(len(r.get("cuts", {})) for r in prev["rows"])
    logp.parent.mkdir(parents=True, exist_ok=True); logp.write_text(json.dumps(prev, indent=1))

    print(json.dumps({"prefix_id": new_id, "turns": total_turns, "dropped": sorted(drop), "reexecuted_from_src_turn": reexec_from,
                      "code_edits": len(edit_log["code_edits"]), "text_edits": len(edit_log.get("text_edits", [])), "reasoning_blanked": [r["turn"] for r in edit_log["reasoning_blanked"]],
                      "result_diffs": len(result_diffs), "final_state_check": final_check, "cuts": outcome["cuts"], "dir": str(fdir)}, indent=1))
    for d in result_diffs:
        print(f"\n--- tool result changed: src turn {d['turn_src']} -> new turn {d['turn_new']}\nBEFORE: {d['before'][:300]!r}\nAFTER:  {d['after'][:300]!r}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
