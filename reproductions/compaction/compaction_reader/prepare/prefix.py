"""Stage A.2: run the prefix agent K times per scenario until the compaction
trigger, keep only prefixes that pass the scenario's state_check, and save the
transcript plus a verified snapshot of the sandbox for each kept prefix."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import CutRule, PrepareConfig
from ..llm.client import LLMClient
from ..prompts_io import read_prompt
from ..sandbox.snapshot import take_snapshot
from ..sandbox.tools import PythonRunner, ToolRegistry, load_tool_specs
from ..scenarios.models import Scenario, materialize_sandbox
from .agent_loop import LoopState, run_agent_loop

log = logging.getLogger(__name__)


@dataclass
class PrefixRecord:
    prefix_id: str
    scenario_id: str
    dir: Path

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def snapshot_dir(self) -> Path:
        return self.dir / "snapshot"

    def load_transcript(self) -> dict[str, Any]:
        return json.loads(self.transcript_path.read_text())


def prefixes_dir(run_dir: Path, scenario_id: str) -> Path:
    return run_dir / "prefixes" / scenario_id


def load_prefixes(run_dir: Path, scenario_id: str) -> list[PrefixRecord]:
    root = prefixes_dir(run_dir, scenario_id)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if (d / "transcript.json").is_file() and (d / "snapshot" / "manifest.json").is_file():
            out.append(PrefixRecord(prefix_id=d.name, scenario_id=scenario_id, dir=d))
    return out


def full_dir(run_dir: Path, scenario_id: str) -> Path:
    return run_dir / "prefixes_full" / scenario_id


def choose_cut_turn(rule: "CutRule", ok_turns: list[bool]) -> tuple[int | None, str]:
    """``ok_turns[t-1]`` says whether the state check passes after turn t (candidate turns only,
    i.e. excluding a final-answer turn). Returns (turn, reason)."""
    n = len(ok_turns)
    if n == 0:
        return None, "no_candidate_turns"
    if rule.rule == "deepest":
        for t in range(n, rule.min_turn - 1, -1):
            if ok_turns[t - 1]:
                return t, "ok"
        return None, "no_turn_passes_state_check"
    t = int(rule.value) if rule.rule == "turn" else max(rule.min_turn, int(round(float(rule.value) * n)))  # type: ignore[arg-type]
    if t < 1 or t > n:
        return None, f"turn_{t}_out_of_range_1..{n}"
    return (t, "ok") if ok_turns[t - 1] else (None, f"state_check_fails_at_turn_{t}")


def write_prefix_dir(d: Path, *, prefix_id: str, scenario_id: str, model: str, result, upto_turn: int, state_check: dict, extra: dict | None = None) -> None:
    """Write transcript.json truncated after ``upto_turn`` (its tool results included)."""
    end = result.turn_ends[upto_turn - 1]
    d.mkdir(parents=True, exist_ok=True)
    usage = {k: sum(int(u.get(k, 0)) for u in result.usage_per_turn[:upto_turn]) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    rec = {
        "prefix_id": prefix_id,
        "scenario_id": scenario_id,
        "model": model,
        "messages": result.messages[:end],
        "tool_events": [e for e in result.tool_events if e["turn"] <= upto_turn],
        "reasoning": result.reasoning[:upto_turn],
        "usage": usage,
        "turns": upto_turn,
        "state_check": state_check,
    }
    if extra:
        rec.update(extra)
    (d / "transcript.json").write_text(json.dumps(rec, indent=1))


def make_trigger(scenario: Scenario):
    trig = scenario.compaction_trigger
    if trig.kind == "after_turn":
        return lambda st: st.turns >= trig.value
    return lambda st: int(st.last_usage.get("prompt_tokens", 0) or 0) >= trig.value


def initial_messages(prompts_dir: Path, scenario: Scenario) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": read_prompt(prompts_dir, "agent", "system.md")},
        {"role": "user", "content": scenario.user_request.strip()},
    ]


async def run_one_prefix(
    cfg: PrepareConfig,
    scenario: Scenario,
    attempt: int,
    client: LLMClient,
    runner: PythonRunner,
) -> tuple[dict[str, Any], PrefixRecord | None]:
    prefix_id = f"{scenario.id}-p{attempt:02d}"
    sandbox = cfg.tmp_root / f"prefix-{prefix_id}"
    materialize_sandbox(scenario, sandbox)
    tools = ToolRegistry(sandbox, runner=runner, specs=load_tool_specs(cfg.prompts_dir))
    messages = initial_messages(cfg.prompts_dir, scenario)
    if cfg.prefix.run_to_completion:
        return await _run_full_trajectory(cfg, scenario, attempt, prefix_id, sandbox, tools, messages, client)
    result = await run_agent_loop(
        client,
        model=cfg.models.prefix,
        messages=messages,
        tools=tools,
        max_turns=cfg.prefix.max_turns,
        tag="prefix",
        should_stop=make_trigger(scenario),
        max_tokens=cfg.prefix.max_tokens,
        temperature=cfg.prefix.temperature,
    )
    outcome: dict[str, Any] = {"prefix_id": prefix_id, "attempt": attempt, "stopped_by": result.stopped_by, "turns": result.turns}
    kept: PrefixRecord | None = None
    if result.stopped_by != "trigger":
        outcome["excluded"] = f"stopped_by_{result.stopped_by}_before_trigger"
    else:
        check = scenario.run_state_check(sandbox, result.messages)
        outcome["state_check"] = check.model_dump()
        if not check.ok:
            outcome["excluded"] = check.reason()
        else:
            d = prefixes_dir(cfg.run_dir, scenario.id) / prefix_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "transcript.json").write_text(
                json.dumps(
                    {
                        "prefix_id": prefix_id,
                        "scenario_id": scenario.id,
                        "model": cfg.models.prefix,
                        "messages": result.messages,
                        "tool_events": result.tool_events,
                        "reasoning": result.reasoning,
                        "usage": result.usage,
                        "turns": result.turns,
                        "state_check": check.model_dump(),
                    },
                    indent=1,
                )
            )
            take_snapshot(sandbox, d / "snapshot")
            kept = PrefixRecord(prefix_id=prefix_id, scenario_id=scenario.id, dir=d)
            outcome["kept"] = True
    if not cfg.sandbox.keep_sandboxes:
        shutil.rmtree(sandbox, ignore_errors=True)
    return outcome, kept


async def _run_full_trajectory(cfg, scenario, attempt, prefix_id, sandbox, tools, messages, client) -> tuple[dict[str, Any], list[PrefixRecord]]:
    """Run to the final answer (or max_turns), snapshot after every turn, then cut post hoc."""
    fdir = full_dir(cfg.run_dir, scenario.id) / prefix_id
    snaps = fdir / "snapshots"
    if (fdir / "transcript.json").is_file():
        # resume: the trajectory already ran to completion; only (re)materialise the cuts
        log.info("prefix %s: full trajectory exists, reusing it", prefix_id)
        result = _load_full_result(fdir)
        if not cfg.sandbox.keep_sandboxes:
            shutil.rmtree(sandbox, ignore_errors=True)
        return _materialize_cuts(cfg, scenario, attempt, prefix_id, fdir, result)
    if fdir.exists():
        shutil.rmtree(fdir)
    snaps.mkdir(parents=True)

    async def after_turn(state: LoopState) -> None:
        take_snapshot(sandbox, snaps / f"turn_{state.turns:02d}")

    result = await run_agent_loop(
        client, model=cfg.models.prefix, messages=messages, tools=tools, max_turns=cfg.prefix.max_turns, tag="prefix",
        should_stop=None, max_tokens=cfg.prefix.max_tokens, temperature=cfg.prefix.temperature, after_turn=after_turn,
    )
    # candidate cut turns: every turn that executed tools (a final-answer turn is not a cut point)
    n_candidates = result.turns - 1 if result.stopped_by == "final_answer" else result.turns
    checks: list[dict[str, Any]] = []
    for t in range(1, n_candidates + 1):
        chk = scenario.run_state_check(snaps / f"turn_{t:02d}" / "files", result.messages[: result.turn_ends[t - 1]])
        checks.append(chk.model_dump())
    # the final answer as it was given without any compaction = the no-compaction baseline
    final_check = scenario.run_state_check(sandbox, result.messages).model_dump()
    take_snapshot(sandbox, fdir / "snapshot_final")
    (fdir / "transcript.json").write_text(json.dumps({
        "prefix_id": prefix_id, "scenario_id": scenario.id, "model": cfg.models.prefix,
        "messages": result.messages, "tool_events": result.tool_events, "reasoning": result.reasoning,
        "usage": result.usage, "usage_per_turn": result.usage_per_turn, "turns": result.turns, "turn_ends": result.turn_ends,
        "stopped_by": result.stopped_by, "finish_reason": result.finish_reason, "final_answer": result.final_answer,
        "final_state_check": final_check, "state_check_by_turn": checks,
    }, indent=1))
    if not cfg.sandbox.keep_sandboxes:
        shutil.rmtree(sandbox, ignore_errors=True)
    return _materialize_cuts(cfg, scenario, attempt, prefix_id, fdir, result)


class _FullResult:
    """LoopResult-shaped view of a saved full trajectory (for resume)."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.messages = d["messages"]; self.tool_events = d["tool_events"]; self.reasoning = d["reasoning"]
        self.usage = d["usage"]; self.usage_per_turn = d["usage_per_turn"]; self.turns = d["turns"]; self.turn_ends = d["turn_ends"]
        self.stopped_by = d["stopped_by"]; self.finish_reason = d.get("finish_reason"); self.final_answer = d.get("final_answer")
        self.state_check_by_turn = d["state_check_by_turn"]; self.final_state_check = d["final_state_check"]


def _load_full_result(fdir: Path) -> _FullResult:
    return _FullResult(json.loads((fdir / "transcript.json").read_text()))


def _materialize_cuts(cfg, scenario, attempt, prefix_id, fdir, result) -> tuple[dict[str, Any], list[PrefixRecord]]:
    snaps = fdir / "snapshots"
    full = json.loads((fdir / "transcript.json").read_text())
    checks = full["state_check_by_turn"]; final_check = full["final_state_check"]
    n_candidates = len(checks)
    ok_turns = [c["present"] and c["unresolved"] and not c["disclosed"] for c in checks]
    outcome: dict[str, Any] = {
        "prefix_id": prefix_id, "attempt": attempt, "stopped_by": result.stopped_by, "turns": result.turns,
        "candidate_turns": n_candidates, "final_disclosed": final_check["disclosed"], "cuts": {},
    }
    kept: list[PrefixRecord] = []
    for rule in cfg.prefix.cuts:
        t, reason = choose_cut_turn(rule, ok_turns)
        cut_id = f"{prefix_id}-{rule.name}"
        if t is None:
            outcome["cuts"][rule.name] = {"turn": None, "excluded": reason}
            continue
        d = prefixes_dir(cfg.run_dir, scenario.id) / cut_id
        if d.exists():
            shutil.rmtree(d)
        write_prefix_dir(d, prefix_id=cut_id, scenario_id=scenario.id, model=cfg.models.prefix, result=result, upto_turn=t,
                         state_check=checks[t - 1], extra={"cut": {"rule": rule.model_dump(), "turn": t, "full_turns": result.turns, "full_prefix_id": prefix_id}})
        shutil.copytree(snaps / f"turn_{t:02d}", d / "snapshot")  # files/ + manifest.json, verified on restore
        kept.append(PrefixRecord(prefix_id=cut_id, scenario_id=scenario.id, dir=d))
        outcome["cuts"][rule.name] = {"turn": t, "kept": True}
    return outcome, kept


async def run_prefixes(
    cfg: PrepareConfig, scenarios: list[Scenario], client: LLMClient, runner: PythonRunner
) -> dict[str, list[PrefixRecord]]:
    kept_by_scenario: dict[str, list[PrefixRecord]] = {}

    async def guarded(sc: Scenario, k: int):
        try:
            return await run_one_prefix(cfg, sc, k, client, runner)
        except Exception as e:  # noqa: BLE001 - one failed attempt must not kill the stage
            log.error("prefix %s-p%02d failed: %s: %s", sc.id, k, type(e).__name__, str(e)[:300])
            return {"prefix_id": f"{sc.id}-p{k:02d}", "attempt": k, "excluded": f"error:{type(e).__name__}"}, None

    # every (scenario, attempt) at once; the client's semaphore bounds the API concurrency
    all_outcomes = await asyncio.gather(*(guarded(sc, k) for sc in scenarios for k in range(cfg.prefix.k)))
    for i, sc in enumerate(scenarios):
        outcomes = all_outcomes[i * cfg.prefix.k:(i + 1) * cfg.prefix.k]
        kept = []
        for _, rec in outcomes:
            if isinstance(rec, list):
                kept.extend(rec)
            elif rec is not None:
                kept.append(rec)
        kept_by_scenario[sc.id] = kept
        d = prefixes_dir(cfg.run_dir, sc.id)
        d.mkdir(parents=True, exist_ok=True)
        log_rows = [o for o, _ in outcomes]
        (d / "prefix_log.json").write_text(json.dumps({"attempts": len(log_rows), "kept": len(kept), "rows": log_rows}, indent=1))
        def _desc(r: dict[str, Any]) -> str:
            if "cuts" in r:
                return f"{r['stopped_by']}@{r['turns']} cuts=" + ",".join(f"{k}:{v.get('turn', v.get('excluded'))}" for k, v in r["cuts"].items())
            return r.get("excluded", "kept")
        log.info("scenario %s: kept %d prefixes from %d attempts (%s)", sc.id, len(kept), len(log_rows), "; ".join(_desc(r) for r in log_rows))
    return kept_by_scenario
