# Portal and shared-wiki runtime

This package retains the original episode loop, World router, simulated tools,
clock/scheduling behavior, task reconstruction, and judgment support used by the two studies.
Some counter support remains an imported dependency of World; unrelated counter campaigns
are excluded. Supported release entry points are the study launchers linked below.

## Setup

Requires Python 3.11+.

```sh
uv sync
uv run ai-collusion-wiki fetch
cp models.example.yaml models.yaml
```

The fetch command downloads the public wiki dump and verifies expanded files against
pinned SHA-256 hashes. The downloaded data and your local configuration are ignored.
Model configuration names in launch commands must match entries in your `models.yaml`.
Keys come from the environment variable specified by `api_key_env`.

- [Portal setup, dry run, and new experiments](../../reproductions/vulnerability-escalation/README.md)
- [Wiki setup, matched conditions, and new experiments](../../reproductions/wiki-coordination/README.md)

Offline tests: `uv run pytest`. Tests exercise the release's retained runtime and
incident spec builders; historical raw-run archives are not required. Some tests
load the public dump, so fetch it first. Model calls are intercepted by these tests.
