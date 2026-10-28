# Open-web coordination

A reconstructed data-lookup task studies agents interacting with wiki content.
[Results](RESULTS.md) describe the recovered 400-episode slow/working grid.
The public launcher creates new experiments using the same spec builders and runtime.
It does not replay private historical run archives or provider migration histories.

From the repository root:

```sh
cd shared/ai-collusion
uv sync
uv run ai-collusion-wiki fetch
uv run python -m scripts.run_wiki --dry-run --run-id wiki-preview
```

The dry run writes the resolved task specs, arms, and initial evaluated-model and
simulator contexts to `runs/wiki-preview/`, without model calls. Both conditions
use the same initial content; slow requests take 14 task seconds and working
requests take 8 container seconds. Both retain quota 25 and cooldown 1,500 task seconds.

For live runs, copy `models.example.yaml` to `models.yaml`, configure your model
entries, and set their credential environment variables. Use a fresh run ID:

```sh
uv run python -m scripts.run_wiki --run-id wiki-example \
  --agent YOUR_AGENT --env-model YOUR_ENVIRONMENT_MODEL --samples 1
```

Live runs make paid model calls. Use `--condition slow` or `--condition working` to
select one arm. Add `--agents 3` for interleaved live agents sharing committed wiki
edits, or add `--isolated-wiki` as a control. Those multi-agent extensions differ
from the historical 400-episode single-agent grid.

To extend the environment, modify `wikitasks/sector61_state_sparse.yaml`, the builders
under `experiments/`, or the new launcher on your branch. Inspect dry-run contexts
before claiming spontaneous discovery: the task includes constructed questions,
wiki/search prefills, supplied peer information, and wiki write instructions.

Offline checks: `uv run pytest tests/test_shared_episodes.py tests/test_data_cooldown.py tests/test_data_quota.py`. Each agent retains private model
history and clocks; only committed wiki edits are shared. Routing and scheduling
are provided by the existing runtime.
