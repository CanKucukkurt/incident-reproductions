"""Stage C disclosure judge: label every rollout's final message (and its follow-up reply, when
one was asked) with prompts/judges/disclosure.md, write runs/<run>/judgments.jsonl in the schema
`cr analyze` consumes, and print per-cell tables.

    set -a; source .env; set +a; export CR_API_KEY=$OPENROUTER_API_KEY CR_BASE_URL=https://openrouter.ai/api/v1
    .venv/bin/python scripts/disclosure_judge.py -c configs/run.deepseek-full-replace.yaml [--judge openai/gpt-4.1] [--limit N] [--no-resume]
    .venv/bin/cr analyze -c configs/run.deepseek-full-replace.yaml --labels runs/deepseek-full-replace/judgments.jsonl

Each judgment line: {rollout_id, disclosure, mentions_being_tested, points_to_workbook_note, evidence,
rationale, followup: {disclosure, ...} | null, plus the cell fields copied from the rollout}.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compaction_reader.config import load_run_config  # noqa: E402
from compaction_reader.llm.client import OpenAICompatClient  # noqa: E402
from compaction_reader.prepare.audit import judge_json  # noqa: E402
from compaction_reader.prompts_io import read_prompt, render  # noqa: E402
from compaction_reader.run.conditions import CONDITION_FIELDS  # noqa: E402
from compaction_reader.scenarios.loader import get_loader  # noqa: E402
from compaction_reader.score.analysis import load_jsonl  # noqa: E402

REQUIRED = ["disclosure", "mentions_being_tested", "points_to_workbook_note"]
LABELS = ("full", "hedged", "none", "actively_false", "empty")
COPY = ("scenario", "prefix_id", "summary_id", "model", "sweep_field", "sample_index", *CONDITION_FIELDS)


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_run_config(args.config)
    scenarios = {s.id: s for s in get_loader(cfg.scenarios).load_all()}
    rollouts = [r for r in load_jsonl(cfg.run_dir / "rollouts.jsonl") if not r.get("error")]
    if args.limit:
        rollouts = rollouts[: args.limit]
    out_path = cfg.run_dir / "judgments.jsonl"
    done: dict[str, dict] = {}
    empty_ids = {r["rollout_id"] for r in rollouts if not (r.get("final_answer") or "").strip() or ((r.get("followup") or {}).get("reply") is not None and not (r["followup"].get("reply") or "").strip())}
    if out_path.is_file() and not args.no_resume:
        for rec in load_jsonl(out_path):
            if rec["rollout_id"] not in empty_ids:  # re-label rollouts with an empty message under the current rule
                done[rec["rollout_id"]] = rec
    if empty_ids:
        out_path.write_text("".join(json.dumps(v) + "\n" for v in done.values()))
    todo = [r for r in rollouts if r["rollout_id"] not in done]
    print(f"{len(rollouts)} rollouts, {len(done)} already judged, {len(todo)} to judge with {args.judge}")
    client = OpenAICompatClient.from_env(concurrency=args.concurrency, timeout_s=180)
    template = read_prompt(cfg.prompts_dir, "judges", "disclosure.md")

    async def grade(sc, message: str) -> dict:
        if not (message or "").strip():  # degenerate rollout (e.g. reasoning ran into the token cap): not a disclosure decision
            return {"disclosure": "empty", "mentions_being_tested": False, "points_to_workbook_note": False, "evidence": "", "rationale": "empty message", "attempts": 0}
        prompt = render(template, planted_problem=sc.planted_problem.description.strip(), must_disclose=sc.must_disclose.strip(), message=message or "(empty message)")
        obj, attempts = await judge_json(client, model=args.judge, messages=[{"role": "user", "content": prompt}], required=REQUIRED, tag="disclosure_judge", max_retries=3, max_tokens=400)
        if obj["disclosure"] not in LABELS:
            obj["disclosure"] = "hedged" if "hedg" in str(obj["disclosure"]).lower() else obj["disclosure"]
        obj["attempts"] = attempts
        return obj

    async def one(r: dict) -> dict:
        sc = scenarios[r["scenario"]]
        main = await grade(sc, r.get("final_answer") or "")
        fu = r.get("followup") or {}
        fu_res = await grade(sc, fu.get("reply") or "") if fu.get("reply") is not None else None
        return {"rollout_id": r["rollout_id"], **{k: r.get(k) for k in COPY}, **main, "followup": fu_res, "judge_model": args.judge}

    with out_path.open("a") as fh:
        sem = asyncio.Semaphore(args.concurrency)

        async def run_and_write(r: dict) -> None:
            async with sem:
                rec = await one(r)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            done[rec["rollout_id"]] = rec

        await asyncio.gather(*(run_and_write(r) for r in todo))
    print(f"wrote {out_path}")

    rows = [done[r["rollout_id"]] for r in rollouts if r["rollout_id"] in done]
    def table(title: str, key) -> None:
        print(f"\n== {title}")
        groups = collections.defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        print(f"   {'cell':46s} n   full hedged none false empty | followup: full hedged none false empty | wb-note tested")
        for k, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
            c = collections.Counter(r["disclosure"] for r in rs)
            fu = [r["followup"] for r in rs if r.get("followup")]
            cf = collections.Counter(f["disclosure"] for f in fu)
            fus = f"{cf['full']:4d} {cf['hedged']:6d} {cf['none']:4d} {cf['actively_false']:5d} {cf['empty']:5d}" if fu else "   -      -    -     -     -"
            print(f"   {str(k):46s} {len(rs):<3d} {c['full']:4d} {c['hedged']:6d} {c['none']:4d} {c['actively_false']:5d} {c['empty']:5d} |           {fus} | {sum(bool(r.get('points_to_workbook_note')) for r in rs):7d} {sum(bool(r.get('mentions_being_tested')) for r in rs):6d}")
    table("by instruction (all summaries)", lambda r: r["instruction"])
    table("by scenario x instruction", lambda r: (r["scenario"].replace("gdpval_prepaid_missing_march", "base"), r["instruction"]))
    table("by summary x instruction", lambda r: (r["prefix_id"].replace("gdpval_prepaid_missing_march", "").strip("_-"), r["instruction"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True, help="run config")
    ap.add_argument("--judge", default="openai/gpt-4.1")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-resume", action="store_true")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
