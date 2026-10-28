"""``cr`` command line. Subcommands: prepare, audit-report, run, judge, analyze,
validate-judges, dry-run."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import PrepareConfig, RunConfig, load_prepare_config, load_run_config
from .errors import CompactionReaderError, ConfigError, SandboxUnavailable
from .llm.client import LLMClient, OpenAICompatClient
from .llm.mock import ScriptedMockClient
from .sandbox.docker_runner import DockerRunner
from .sandbox.tools import FakeRunner, PythonRunner
from .scenarios.loader import get_loader

log = logging.getLogger("cr")


def make_client(llm_cfg, mock: str | None) -> LLMClient:
    if mock:
        return ScriptedMockClient.from_json(mock)
    if getattr(llm_cfg, "api", "chat") == "responses":
        from .llm.responses_client import OpenAIResponsesClient

        return OpenAIResponsesClient.from_env(
            concurrency=llm_cfg.concurrency, max_retries=llm_cfg.max_retries, timeout_s=llm_cfg.timeout_s, extra_body=llm_cfg.extra_body
        )
    return OpenAICompatClient.from_env(
        concurrency=llm_cfg.concurrency, max_retries=llm_cfg.max_retries, timeout_s=llm_cfg.timeout_s, extra_body=llm_cfg.extra_body
    )


def make_runner(sandbox_cfg, mock: str | None) -> PythonRunner:
    if sandbox_cfg.runner == "fake":
        if not mock:
            raise ConfigError("sandbox.runner=fake is only allowed together with --mock; real model code must run in Docker")
        return FakeRunner()
    runner = DockerRunner(
        image=sandbox_cfg.image, cpus=sandbox_cfg.cpus, memory=sandbox_cfg.memory, pids_limit=sandbox_cfg.pids_limit, timeout_s=sandbox_cfg.timeout_s
    )
    runner.check_available()  # raises SandboxUnavailable before any API call
    return runner


def _scenarios(cfg):
    return get_loader(cfg.scenarios).load_all()


async def cmd_locate_plans(args) -> int:
    from .prepare.audit import load_audit_rows
    from .prepare.locate import locate_plans
    from .prepare.summarize import load_summaries

    cfg: PrepareConfig = load_prepare_config(args.config)
    scenarios = {s.id: s for s in _scenarios(cfg)}
    summaries = load_summaries(cfg.run_dir)
    if args.summary:
        want = set(args.summary)
        summaries = [s for s in summaries if s.summary_id in want]
        missing = want - {s.summary_id for s in summaries}
        if missing:
            raise ConfigError(f"unknown summary ids: {sorted(missing)}")
    else:  # default: the summaries the audit excluded only for carrying a natural disclose plan
        rows = load_audit_rows(cfg.run_dir)
        summaries = [s for s in summaries if rows.get(s.summary_id, {}).get("exclusion_reason") == "natural_disclose_instruction"]
    if not summaries:
        raise ConfigError("no summaries selected")
    client = make_client(cfg.llm, args.mock)
    res = await locate_plans(cfg, summaries, scenarios, client, model=args.judge)
    n_ok = sum(1 for r in res.values() if r["disclose_plans"] and not r["conceal_plans"])
    n_unv = sum(len(r["unverified"]) for r in res.values())
    print(f"located plans in {len(summaries)} summaries: {n_ok} eligible for replace, {n_unv} sentence(s) not found verbatim")
    print(f"wrote {cfg.run_dir}/audit/plan_sentences.json and eligible_replace.json")
    return 0


async def cmd_prepare(args) -> int:
    from .prepare.audit import audit_summaries
    from .prepare.prefix import load_prefixes, run_prefixes
    from .prepare.summarize import load_summaries, write_summaries

    cfg: PrepareConfig = load_prepare_config(args.config)
    scenarios = _scenarios(cfg)
    client = make_client(cfg.llm, args.mock)
    stages = ["prefixes", "summaries", "audit"] if args.stage == "all" else [args.stage]
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "prepare_config.json").write_text(json.dumps(cfg.model_dump(mode="json"), indent=1))
    if "prefixes" in stages:
        runner = make_runner(cfg.sandbox, args.mock)
        kept = await run_prefixes(cfg, scenarios, client, runner)
        for sid, recs in kept.items():
            print(f"{sid}: kept {len(recs)}/{cfg.prefix.k} prefixes")
    if "summaries" in stages:
        prefixes = [p for s in scenarios for p in load_prefixes(cfg.run_dir, s.id)]
        if not prefixes:
            raise ConfigError("no kept prefixes; run the prefixes stage first")
        recs = await write_summaries(cfg, prefixes, client)
        print(f"wrote {len(recs)} summaries")
    if "audit" in stages:
        summaries = load_summaries(cfg.run_dir)
        if not summaries:
            raise ConfigError("no summaries; run the summaries stage first")
        res = await audit_summaries(cfg, summaries, {s.id: s for s in scenarios}, client)
        print(f"audit: {len(res.eligible)}/{len(summaries)} eligible; excluded: {dict(res.exclusion_counts()) or 'none'}")
        print(f"report: {cfg.run_dir / 'audit' / 'audit_report.md'}")
    return 0


def cmd_audit_report(args) -> int:
    from .prepare.audit import load_audit_csv, render_report

    cfg = load_prepare_config(args.config)
    csv_path = cfg.run_dir / "audit" / "audit.csv"
    if not csv_path.is_file():
        raise ConfigError(f"no audit.csv at {csv_path}")
    report = render_report(load_audit_csv(csv_path))
    (cfg.run_dir / "audit" / "audit_report.md").write_text(report)
    print(report)
    return 0


async def cmd_run(args) -> int:
    from .run.successor import load_run_context, run_experiment

    cfg: RunConfig = load_run_config(args.config)
    scenarios = _scenarios(cfg)
    runner = make_runner(cfg.sandbox, args.mock)  # sandbox check first: fail before any API setup
    client = make_client(cfg.llm, args.mock)
    ctx = load_run_context(cfg, scenarios, client, runner)
    path = await run_experiment(ctx, resume=not args.no_resume, retry_errors=args.retry_errors, limit=args.limit)
    print(f"rollouts: {path}")
    return 0


def cmd_dry_run(args) -> int:
    from .run.assembler import assemble
    from .run.conditions import expand_cells
    from .run.instructions import summary_diff
    from .run.successor import eligible_summaries

    cfg = load_run_config(args.config)
    scenarios = {s.id: s for s in _scenarios(cfg)}
    summaries = eligible_summaries(cfg)
    if args.summary:
        matches = [s for s in summaries if s.summary_id == args.summary]
        if not matches:
            raise ConfigError(f"summary {args.summary} is not eligible or not found")
        summary = matches[0]
    else:
        summary = summaries[0]
    cells = expand_cells(cfg)
    cell = next((c for c in cells if c.compaction_variant == summary.compaction_variant), cells[0]).model_copy()
    overrides = {"instruction": args.instruction} if args.instruction else {}
    for kv in args.set or []:
        k, v = kv.split("=", 1)
        overrides[k] = v
    cell = cell.model_copy(update=overrides)
    sc = scenarios[summary.scenario_id]
    from .prepare.prefix import load_prefixes

    prefix = next((p for p in load_prefixes(cfg.prepare_dir, sc.id) if p.prefix_id == summary.prefix_id), None)
    transcript_messages = prefix.load_transcript()["messages"] if prefix else []
    from .run.successor import plan_sentences_for

    ps = plan_sentences_for(cfg, summary.summary_id)
    asm = assemble(prompts_dir=cfg.prompts_dir, scenario=sc, summary_text=summary.text, cell=cell, successor_model=summary.model,
                   other_model_name=cfg.successor.other_model_name, transcript_messages=transcript_messages, plan_sentences=ps)
    base_asm = assemble(prompts_dir=cfg.prompts_dir, scenario=sc, summary_text=summary.text, cell=cell.model_copy(update={"instruction": "none"}),
                        successor_model=summary.model, other_model_name=cfg.successor.other_model_name, transcript_messages=transcript_messages, plan_sentences=ps)
    print(f"# summary {summary.summary_id}  cell: {cell.model_dump()}")
    print("\n# assembled messages")
    print(json.dumps(asm.messages, indent=1))
    print(f"\n# tools: {[t['function']['name'] for t in asm.tools] if asm.tools else 'none (final_answer_only)'}")
    print("\n# summary diff (none -> {})".format(cell.instruction))
    print(summary_diff(base_asm.summary_text, asm.summary_text, "none", cell.instruction) or "(identical)")
    return 0


def cmd_analyze(args) -> int:
    from .score.analysis import analyze

    cfg = load_run_config(args.config)
    labels = Path(args.labels) if args.labels else cfg.run_dir / "judgments.jsonl"
    if not labels.is_file():
        raise ConfigError(f"labels file not found: {labels} (judges are not built yet; supply --labels with the documented schema)")
    out = analyze(cfg.run_dir / "rollouts.jsonl", labels, cfg.run_dir / "analysis")
    print(json.dumps(out, indent=1))
    print(f"report: {cfg.run_dir / 'analysis' / 'analysis.md'}")
    return 0


def cmd_held_off(args) -> int:
    print(f"`cr {args.cmd}` is not implemented yet: Stage C judges are held off per the brief owner. "
          f"Rubrics will live in prompts/judges/ and output judgments.jsonl (see compaction_reader/score/analysis.py for the schema).")
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cr", description="compaction-reader harness")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("-c", "--config", required=True)
        sp.add_argument("--mock", help="path to a scripted mock client JSON; no real API calls are made")

    sp = sub.add_parser("prepare", help="Stage A: prefixes, summaries, audit")
    common(sp)
    sp.add_argument("--stage", choices=["all", "prefixes", "summaries", "audit"], default="all")
    sp = sub.add_parser("locate-plans", help="judge pass: extract each summary's own plan about telling the user, verbatim (for edit_mode: replace)")
    sp.add_argument("-c", "--config", required=True)
    sp.add_argument("--summary", action="append", help="summary id (repeatable); default: all natural_disclose exclusions")
    sp.add_argument("--judge", help="judge model (default: models.audit)")
    sp.add_argument("--mock")
    sp = sub.add_parser("audit-report", help="regenerate audit_report.md from audit.csv")
    sp.add_argument("-c", "--config", required=True)
    sp = sub.add_parser("run", help="Stage B: successor rollouts")
    common(sp)
    sp.add_argument("--no-resume", action="store_true")
    sp.add_argument("--retry-errors", action="store_true")
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("dry-run", help="print assembled messages and summary diff for one cell; no API calls")
    sp.add_argument("-c", "--config", required=True)
    sp.add_argument("--summary")
    sp.add_argument("--instruction")
    sp.add_argument("--set", action="append", metavar="FIELD=VALUE")
    sp = sub.add_parser("analyze", help="Stage C: rates, Wilson intervals, contrasts")
    sp.add_argument("-c", "--config", required=True)
    sp.add_argument("--labels")
    for name in ("judge", "validate-judges"):
        sp = sub.add_parser(name, help="held off (not implemented)")
        sp.add_argument("-c", "--config", required=False)
        sp.add_argument("--mock")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.cmd == "prepare":
            return asyncio.run(cmd_prepare(args))
        if args.cmd == "locate-plans":
            return asyncio.run(cmd_locate_plans(args))
        if args.cmd == "audit-report":
            return cmd_audit_report(args)
        if args.cmd == "run":
            return asyncio.run(cmd_run(args))
        if args.cmd == "dry-run":
            return cmd_dry_run(args)
        if args.cmd == "analyze":
            return cmd_analyze(args)
        return cmd_held_off(args)
    except SandboxUnavailable as e:
        print(f"SANDBOX UNAVAILABLE: {e}", file=sys.stderr)
        return 3
    except CompactionReaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
