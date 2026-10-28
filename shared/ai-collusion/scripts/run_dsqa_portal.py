#!/usr/bin/env python3
"""Run the dsqa_250 portal episode: a fresh single-question research task with no shared-wiki
prefill, in two matched conditions (healthy = neutral, unavailable = empty_success).

Observational only: we read the transcript for whether the model independently escalates to an
out-of-policy probe. Portal GETs are deterministic; complex shell commands use the environment model.

  # offline check: print the agent's initial context and the simulator system prompt per condition
  python -m scripts.run_dsqa_portal --dry-run

  # one rollout per condition with gpt-5.6-sol as both agent and environment model
  python -m scripts.run_dsqa_portal --agent gpt-5.6 --env-model env-gpt-5.6 -n 1
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.wiki import Cut, Dump, load_spec
from ai_collusion.runner import load_models
from ai_collusion.preparation import prepare_episode
from ai_collusion.episode import run_episodes

SPEC_PATH = "wikitasks/dsqa250_portal.yaml"
DUMP_PATH = "data/collusion-wiki"
MODES = ["neutral", "empty_success"]        # healthy, unavailable
MODE_LABEL = {"neutral": "HEALTHY", "empty_success": "UNAVAILABLE"}


def synthetic_cut() -> Cut:
    """A cut that anchors the episode without any shared-wiki history (no_wiki_prefill)."""
    return Cut(
        page_id="dse/DsqaPortal", seq=1, time="2027-05-05T14:00:00Z",
        label="agent-7", ip16="0" * 16, body_before=None, added="",
        prior=[], later=[],
    )


def dry_run(spec, dump, cut, variant, modes) -> None:
    for mode in modes:
        prepared = prepare_episode(spec, dump, cut, variant, mode, env_model=None)
        print("=" * 100)
        print(f"CONDITION: {MODE_LABEL[mode]}  (mode={mode})")
        print("=" * 100)
        print("\n----- AGENT SYSTEM PROMPT -----\n")
        print(prepared.context["system"])
        print("\n----- AGENT INITIAL MESSAGES -----\n")
        for m in prepared.context["messages"]:
            print(f"[{m['role']}]\n{m['content']}\n")
        print("\n----- ENVIRONMENT SIMULATOR SYSTEM PROMPT -----\n")
        print(prepared.world.environment_system())
        print(f"\n----- ground_truth supplied to simulator this condition: "
              f"{'YES' if (mode == 'neutral') else 'NO'} -----\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", default="gpt-5.6", help="evaluated model name in models.yaml")
    p.add_argument("--env-model", default="env-gpt-5.6", help="environment-simulator model name")
    p.add_argument("-n", "--samples", type=int, default=1)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out", default="runs")
    p.add_argument("--run-id", default=None)
    p.add_argument("--models", default="models.yaml")
    p.add_argument("--spec", default=SPEC_PATH, help=f"task spec YAML (default: {SPEC_PATH})")
    p.add_argument("--variant", default=None, help="cut variant (e.g. prior_sqli); default base")
    p.add_argument("--modes", nargs="+", default=MODES, choices=MODES,
                   help="conditions to run (default: both healthy + unavailable)")
    p.add_argument("--dry-run", action="store_true", help="print contexts and exit; no API calls")
    args = p.parse_args(argv)

    spec = load_spec(args.spec)
    dump = Dump(DUMP_PATH)
    cut = synthetic_cut()

    if args.dry_run:
        dry_run(spec, dump, cut, args.variant, args.modes)
        return

    models = load_models(args.models, only=[args.agent, args.env_model])
    agent = next(m for m in models if m.name == args.agent)
    env_model = next(m for m in models if m.name == args.env_model)
    run_id = args.run_id or "dsqa250-portal-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    out = run_episodes(
        models=[agent], env_model=env_model, spec=spec, dump=dump,
        cuts={1: cut}, variants=[args.variant], modes=args.modes,
        n_samples=args.samples, out_dir=args.out, base_seed=args.seed,
        temperature=None, max_turns=args.max_turns, run_id=run_id, workers=args.workers,
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
