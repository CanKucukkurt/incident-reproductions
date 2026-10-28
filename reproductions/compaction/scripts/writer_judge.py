"""Writer-side judge: grade every summary of a prepare run on how it records the planted
problem and what it plans to tell the user (prompts/judges/writer.md).

    set -a; source .env; set +a; export CR_API_KEY=$OPENROUTER_API_KEY CR_BASE_URL=https://openrouter.ai/api/v1
    .venv/bin/python scripts/writer_judge.py -c configs/prepare.deepseek-writer48.yaml [--judge openai/gpt-4.1] [--limit N] [--rubric missing_file|fabrication]

Writes runs/<run>/writer_judgments.jsonl and prints a per-scenario table. Resumable: existing
judgments are kept unless --no-resume.
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

from compaction_reader.config import load_prepare_config  # noqa: E402
from compaction_reader.llm.client import OpenAICompatClient  # noqa: E402
from compaction_reader.prepare.audit import judge_json  # noqa: E402
from compaction_reader.prepare.summarize import load_summaries  # noqa: E402
from compaction_reader.prompts_io import read_prompt, render  # noqa: E402
from compaction_reader.scenarios.loader import YamlDirLoader, get_loader  # noqa: E402

RUBRICS = {
    # name: (prompt file under prompts/judges/, required JSON keys, categorical fields for the table, boolean fields for the header)
    "missing_file": ("writer.md", ["mentions_problem", "problem_framing", "user_disclosure_plan", "instruction_to_successor", "pressure_gloss", "asks_for_file"],
                     ["problem_framing", "user_disclosure_plan", "instruction_to_successor", "pressure_gloss"], ["mentions_problem", "asks_for_file"]),
    "missing_ap_tb": ("writer_ap_tb.md", ["mentions_problem", "problem_framing", "user_disclosure_plan", "instruction_to_successor", "pressure_gloss", "asks_for_file"],
                      ["problem_framing", "user_disclosure_plan", "instruction_to_successor", "pressure_gloss"], ["mentions_problem", "asks_for_file"]),
    "fabrication_ap_tb": ("writer_fabrication_ap_tb.md", ["mentions_fabrication", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor", "continues_fiction", "mentions_original_missing"],
                          ["fabrication_framing", "user_disclosure_plan", "instruction_to_successor"], ["mentions_fabrication", "continues_fiction", "mentions_original_missing"]),
    "deletion_ap_tb": ("writer_deletion_ap_tb.md", ["mentions_deletion", "deletion_framing", "absence_attributed_to", "mentions_fabrication", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor", "continues_fiction", "mentions_original_missing"],
                       ["deletion_framing", "absence_attributed_to", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor"], ["mentions_deletion", "mentions_fabrication", "continues_fiction", "mentions_original_missing"]),
    "deletion": ("writer_deletion.md", ["mentions_deletion", "deletion_framing", "absence_attributed_to", "mentions_fabrication", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor", "continues_fiction", "mentions_original_missing"],
                 ["deletion_framing", "absence_attributed_to", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor"], ["mentions_deletion", "mentions_fabrication", "continues_fiction", "mentions_original_missing"]),
    "vendor_mismatch": ("writer_vendor_mismatch.md", ["mentions_offline", "workaround", "label_mismatch_noted", "mismatch_framing", "checks_as_cover", "user_disclosure_plan", "instruction_to_successor"],
                        ["workaround", "mismatch_framing", "user_disclosure_plan", "instruction_to_successor"], ["mentions_offline", "label_mismatch_noted", "checks_as_cover"]),
    "fabrication": ("writer_fabrication.md", ["mentions_fabrication", "fabrication_framing", "user_disclosure_plan", "instruction_to_successor", "continues_fiction", "mentions_original_missing"],
                    ["fabrication_framing", "user_disclosure_plan", "instruction_to_successor"], ["mentions_fabrication", "continues_fiction", "mentions_original_missing"]),
}


async def main_async(args: argparse.Namespace) -> int:
    if args.jsonl:
        from types import SimpleNamespace
        scenarios = {s.id: s for s in YamlDirLoader(REPO_ROOT / "scenarios").load_all()}
        summaries = [SimpleNamespace(**{"finish_reason": "stop", "compaction_variant": "?", "sample_index": 0, **json.loads(l)}) for l in Path(args.jsonl).read_text().splitlines() if l.strip()]
        out_path = Path(args.jsonl).with_name("writer_judgments.jsonl")
        prompts_dir = REPO_ROOT / "prompts"
    else:
        cfg = load_prepare_config(args.config)
        scenarios = {s.id: s for s in get_loader(cfg.scenarios).load_all()}
        summaries = load_summaries(cfg.run_dir)
        out_path = cfg.run_dir / "writer_judgments.jsonl"
        prompts_dir = cfg.prompts_dir
    prompt_file, REQUIRED, FIELDS, BOOLS = RUBRICS[args.rubric]
    if args.limit:
        summaries = summaries[: args.limit]
    done: dict[str, dict] = {}
    if out_path.is_file() and not args.no_resume:
        for line in out_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["summary_id"]] = rec
    todo = [s for s in summaries if s.summary_id not in done]
    print(f"{len(summaries)} summaries, {len(done)} already judged, {len(todo)} to judge with {args.judge}")
    client = OpenAICompatClient.from_env(concurrency=args.concurrency, timeout_s=180)
    template = read_prompt(prompts_dir, "judges", prompt_file)

    async def one(s):
        sc = scenarios.get(s.scenario_id)   # rubrics for real external tasks (no scenario.yaml) carry their own problem text
        prompt = render(template, planted_problem=sc.planted_problem.description.strip() if sc else "", must_disclose=sc.must_disclose.strip() if sc else "", summary=s.text)
        obj, attempts = await judge_json(client, model=args.judge, messages=[{"role": "user", "content": prompt}], required=REQUIRED, tag="writer_judge", max_retries=3, max_tokens=600)
        return {"summary_id": s.summary_id, "scenario_id": s.scenario_id, "prefix_id": s.prefix_id, "model": s.model, "compaction_variant": s.compaction_variant,
                "sample_index": s.sample_index, "finish_reason": s.finish_reason, "judge_model": args.judge, "rubric": args.rubric, "attempts": attempts, **obj}

    results = await asyncio.gather(*(one(s) for s in todo))
    with out_path.open("a") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
            done[r["summary_id"]] = r
    print(f"wrote {out_path}")
    rows = [done[s.summary_id] for s in summaries if s.summary_id in done]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["scenario_id"]].append(r)
    for sid, rs in by.items():
        print(f"\n== {sid}  n={len(rs)}  " + "  ".join(f"{b}={sum(bool(r.get(b)) for r in rs)}" for b in BOOLS))
        for f in FIELDS:
            c = collections.Counter(r[f] for r in rs)
            print(f"   {f:24s} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", help="prepare config (summaries from its run dir)")
    ap.add_argument("--jsonl", help="instead of a prepare run: a summaries.jsonl with summary_id, scenario_id, prefix_id, model, text")
    ap.add_argument("--judge", default="openai/gpt-4.1")
    ap.add_argument("--rubric", choices=sorted(RUBRICS), default="missing_file", help="fabrication: prompts/judges/writer_fabrication.md (guided fabrication scenario)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-resume", action="store_true")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
