"""Start new matched slow/working experiments using the released historical spec builders."""
from __future__ import annotations
import argparse
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import yaml
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from ai_collusion.runner import load_models
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.request_cooldown import ARM, CUT, VARIANT, build_spec, matched_brave_prefill

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', required=True)
    p.add_argument('--condition', choices=['slow', 'working', 'both'], default='both')
    p.add_argument('--agent', default='agent')
    p.add_argument('--env-model', default='environment')
    p.add_argument('--models', default='models.yaml')
    p.add_argument('--samples', type=int, default=1)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--agents', type=int, default=1)
    p.add_argument('--isolated-wiki', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    if args.samples < 1 or args.agents < 1:
        p.error('--samples and --agents must be positive')
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in {'.', '..'}:
        p.error('--run-id must be one directory name')
    output = ROOT / 'runs' / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, provenance = build_spec(yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text()), dump)
    working = copy.deepcopy(built['cuts'][CUT]['variants'][VARIANT])
    if working.pop('data_single_state_task_s') != 14 or working['data_single_state_s'] != 8:
        raise ValueError('Unexpected baseline timing')
    working_name = 'sparse_normal_data_v7_coordination_quota25_cooldown1500'
    built['cuts'][CUT]['variants'][working_name] = working
    spec_path = output / 'spec.yaml'
    spec_path.write_text(yaml.safe_dump(built, sort_keys=False, allow_unicode=True))
    spec = load_spec(spec_path)
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    arms = [('slow', ARM), ('working', replace(ARM, id='normal-data-coordination-quota25-cooldown1500-v7', variant=working_name))]
    previews = {}
    for condition, arm in arms:
        prepared = matched_brave_prefill(prepare_episode(
            spec, dump, cut, arm.variant, arm.mode, None, args.seed,
            num_live_problems=arm.num_live_problems,
            wiki_write_instructions=arm.wiki_write_instructions, hint=arm.hint))
        previews[condition] = {'context': prepared.context, 'context_sha256': prepared.context_sha256,
                               'environment_system': prepared.world.environment_system(),
                               'resolved_config': prepared.resolved_config, 'arm': asdict(arm)}
    if previews['slow']['context'] != previews['working']['context']:
        raise ValueError('Initial model contexts must match across arms')
    (output / 'preview.json').write_text(json.dumps({'provenance': provenance, 'conditions': previews}, indent=2) + '\n')
    if args.dry_run:
        print(f'Prepared matched contexts in {output}; no model calls.')
        return
    models = load_models(args.models, only=list(dict.fromkeys([args.agent, args.env_model])))
    agent = next(m for m in models if m.name == args.agent)
    environment = next(m for m in models if m.name == args.env_model)
    for condition, arm in arms:
        if args.condition not in {'both', condition}:
            continue
        result = run_episodes(models=[agent], env_model=environment, spec=spec, dump=dump,
                              cuts={4: cut}, variants=[arm.variant], modes=[arm.mode],
                              n_samples=args.samples, out_dir=str(output), base_seed=args.seed,
                              temperature=None, max_turns=None, run_id=condition, workers=1,
                              arms=[arm], prepare_hook=matched_brave_prefill, agents=args.agents,
                              shared_wiki=not args.isolated_wiki)
        print(result)


if __name__ == '__main__':
    main()
