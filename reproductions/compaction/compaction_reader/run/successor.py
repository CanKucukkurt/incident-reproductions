"""Stage B: run the successor agent for each (eligible summary x cell x sample)."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import BaseCell, RunConfig
from ..llm.client import LLMClient
from ..prepare.agent_loop import run_agent_loop
from ..prepare.audit import load_eligible_ids
from ..prepare.locate import load_eligible_replace_ids, load_plan_sentences
from ..prepare.prefix import PrefixRecord, load_prefixes
from ..prepare.summarize import SummaryRecord, load_summaries
from ..prompts_io import read_prompt
from ..sandbox.snapshot import restore_snapshot, tree_manifest
from ..sandbox.tools import PythonRunner, ToolRegistry
from ..scenarios.models import Scenario
from .assembler import assemble
from .conditions import CONDITION_FIELDS, cell_label, expand_cells, sweep_field_of
from .rollout_log import JsonlLog, rollout_id

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    cfg: RunConfig
    scenarios: dict[str, Scenario]
    prefixes: dict[str, PrefixRecord]  # prefix_id -> record
    client: LLMClient
    runner: PythonRunner | None


@dataclass
class Job:
    summary: SummaryRecord
    cell: BaseCell
    sample_index: int

    @property
    def rollout_id(self) -> str:
        return rollout_id(
            scenario=self.summary.scenario_id,
            prefix_id=self.summary.prefix_id,
            summary_id=self.summary.summary_id,
            model=self.summary.model,
            sample_index=self.sample_index,
            **{k: getattr(self.cell, k) for k in CONDITION_FIELDS},
        )


def eligible_summaries(cfg: RunConfig) -> list[SummaryRecord]:
    """Summaries from the prepare run that the audit marked eligible. Nothing else reaches Stage B."""
    if cfg.base.edit_mode == "replace":
        ids = load_eligible_replace_ids(cfg.prepare_dir)
        if not ids:
            raise RuntimeError(f"no summaries in {cfg.prepare_dir}/audit/eligible_replace.json; run `cr locate-plans` first")
    else:
        ids = load_eligible_ids(cfg.prepare_dir)
        if not ids:
            raise RuntimeError(f"no eligible summaries in {cfg.prepare_dir}/audit/eligible.json; run `cr prepare` first")
    out = [s for s in load_summaries(cfg.prepare_dir) if s.summary_id in ids]
    if cfg.models_under_test:
        out = [s for s in out if s.model in cfg.models_under_test]
    if cfg.only_summaries:
        out = [s for s in out if s.summary_id in set(cfg.only_summaries)]
    if cfg.max_summaries_per_prefix:
        by_prefix: dict[tuple[str, str, str], int] = {}
        kept = []
        for s in sorted(out, key=lambda s: s.summary_id):
            key = (s.prefix_id, s.model, s.compaction_variant)
            if by_prefix.get(key, 0) < cfg.max_summaries_per_prefix:
                by_prefix[key] = by_prefix.get(key, 0) + 1
                kept.append(s)
        out = kept
    return out


_PLAN_CACHE: dict[Path, dict[str, Any]] = {}


def plan_sentences_for(cfg: RunConfig, summary_id: str) -> list[dict[str, str]]:
    """The located disclose plans ({sentence, clause}) for a summary (empty if none were located)."""
    key = Path(cfg.prepare_dir)
    if key not in _PLAN_CACHE:
        _PLAN_CACHE[key] = load_plan_sentences(cfg.prepare_dir)
    return list(_PLAN_CACHE[key].get(summary_id, {}).get("disclose_plans") or [])


def build_jobs(cfg: RunConfig, summaries: list[SummaryRecord]) -> list[Job]:
    jobs = []
    for cell in expand_cells(cfg):
        for s in summaries:
            if s.compaction_variant != cell.compaction_variant:
                continue
            for i in range(cfg.samples_per_cell):
                jobs.append(Job(summary=s, cell=cell, sample_index=i))
    return jobs


def followup_applies(cfg: RunConfig, cell: BaseCell) -> bool:
    if not cfg.followup.enabled:
        return False
    for k, allowed in cfg.followup.only_for.items():
        if getattr(cell, k) not in allowed:
            return False
    return True


async def run_rollout(ctx: RunContext, job: Job) -> dict[str, Any]:
    cfg = ctx.cfg
    s = job.summary
    scenario = ctx.scenarios[s.scenario_id]
    prefix = ctx.prefixes[s.prefix_id]
    rid = job.rollout_id
    asm = assemble(
        prompts_dir=cfg.prompts_dir, scenario=scenario, summary_text=s.text, cell=job.cell,
        successor_model=s.model, other_model_name=cfg.successor.other_model_name,
        transcript_messages=prefix.load_transcript()["messages"],
        plan_sentences=plan_sentences_for(cfg, s.summary_id),
    )
    record: dict[str, Any] = {
        "rollout_id": rid,
        "run_name": cfg.run_name,
        "scenario": s.scenario_id,
        "prefix_id": s.prefix_id,
        "summary_id": s.summary_id,
        **{k: getattr(job.cell, k) for k in CONDITION_FIELDS},
        "cell": cell_label(job.cell),
        "sweep_field": sweep_field_of(job.cell, cfg.base),
        "sample_index": job.sample_index,
        "model": s.model,
        "summary_text_used": asm.summary_text,
        "instruction_line": asm.instruction_line,
        "replaced_sentences": plan_sentences_for(cfg, s.summary_id) if job.cell.edit_mode == "replace" and asm.instruction_line else None,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    sandbox = cfg.tmp_root / rid
    try:
        restore_snapshot(prefix.snapshot_dir, sandbox)
        tools = None
        if asm.tools is not None:
            tools = ToolRegistry(sandbox, runner=ctx.runner, specs=asm.tools)
        result = await run_agent_loop(
            ctx.client, model=s.model, messages=asm.messages, tools=tools,
            max_turns=1 if job.cell.mode == "final_answer_only" else cfg.successor.max_turns,
            tag="successor", max_tokens=cfg.successor.max_tokens, temperature=cfg.successor.temperature,
            max_degenerate_retries=cfg.successor.max_degenerate_retries,
        )
        record.update(
            {
                "messages": result.messages,
                "tool_calls": result.tool_events,
                "reasoning": result.reasoning,
                "final_answer": result.final_answer,
                "finish_reason": result.finish_reason,
                "stopped_by": result.stopped_by,
                "turns": result.turns,
                "usage": result.usage,
                "sandbox_final_manifest": tree_manifest(sandbox),
            }
        )
        if result.stopped_by == "degenerate":
            record["error"] = f"degenerate_final_answer:{result.finish_reason}"  # not a disclosure decision; `--retry-errors` re-runs it
        elif followup_applies(cfg, job.cell):
            question = read_prompt(cfg.prompts_dir, "followup", f"{cfg.followup.prompt}.md").strip()
            result.messages.append({"role": "user", "content": question})
            fu_tools = tools if cfg.followup.tools else None
            fu = await run_agent_loop(
                ctx.client, model=s.model, messages=result.messages, tools=fu_tools,
                max_turns=cfg.successor.max_turns if cfg.followup.tools else 1,
                tag="followup", max_tokens=cfg.successor.max_tokens, temperature=cfg.successor.temperature,
                max_degenerate_retries=cfg.successor.max_degenerate_retries,
            )
            record["followup"] = {
                "question": question, "tools": cfg.followup.tools, "reply": fu.final_answer,
                "finish_reason": fu.finish_reason, "tool_calls": fu.tool_events, "reasoning": fu.reasoning, "usage": fu.usage,
            }
            record["messages"] = fu.messages
    except Exception as e:  # noqa: BLE001 - logged per rollout, run continues
        log.exception("rollout %s failed", rid)
        record["error"] = f"{type(e).__name__}: {e}"
    finally:
        if not cfg.sandbox.keep_sandboxes:
            shutil.rmtree(sandbox, ignore_errors=True)
    return record


async def run_experiment(ctx: RunContext, *, resume: bool = True, retry_errors: bool = False, limit: int | None = None) -> Path:
    cfg = ctx.cfg
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    log_ = JsonlLog(cfg.run_dir / "rollouts.jsonl")
    (cfg.run_dir / "run_config.json").write_text(json.dumps(cfg.model_dump(mode="json"), indent=1))
    summaries = eligible_summaries(cfg)
    jobs = build_jobs(cfg, summaries)
    for j in jobs:  # no API calls: catches a missing problem_ref / anchor / plan sentence before anything runs
        assemble(prompts_dir=cfg.prompts_dir, scenario=ctx.scenarios[j.summary.scenario_id], summary_text=j.summary.text,
                 cell=j.cell, successor_model=j.summary.model, other_model_name=cfg.successor.other_model_name,
                 plan_sentences=plan_sentences_for(cfg, j.summary.summary_id))
    done = log_.completed_ids(include_errors=not retry_errors) if resume else set()
    todo = [j for j in jobs if j.rollout_id not in done]
    if limit is not None:
        todo = todo[:limit]
    log.info("run %s: %d jobs total, %d already done, %d to run", cfg.run_name, len(jobs), len(jobs) - len(todo), len(todo))
    sem = asyncio.Semaphore(cfg.max_parallel_rollouts)
    lock = asyncio.Lock()

    async def one(job: Job) -> None:
        async with sem:
            rec = await run_rollout(ctx, job)
        async with lock:
            log_.append(rec)

    await asyncio.gather(*(one(j) for j in todo))
    return log_.path


def load_run_context(cfg: RunConfig, scenarios: list[Scenario], client: LLMClient, runner: PythonRunner | None) -> RunContext:
    sc = {s.id: s for s in scenarios}
    prefixes = {p.prefix_id: p for sid in sc for p in load_prefixes(cfg.prepare_dir, sid)}
    return RunContext(cfg=cfg, scenarios=sc, prefixes=prefixes, client=client, runner=runner)
