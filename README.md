# Incident reproductions

Environments and results for four studies of model behavior, by Can, Ethan, Frank, and Nourya.
Fork this repository to modify a scenario, prompt, condition, or model configuration.

| Study | Environment and instructions | Released results |
| --- | --- | --- |
| Compaction concealment | [Compaction](reproductions/compaction/README.md) | [Exploratory summary](reproductions/compaction/RESULTS.md) |
| Vulnerability-driven escalation | [Portal](reproductions/vulnerability-escalation/README.md) | [Judgments and limitations](reproductions/vulnerability-escalation/RESULTS.md) |
| Open-web coordination | [Wiki](reproductions/wiki-coordination/README.md) | [400-episode summary](reproductions/wiki-coordination/RESULTS.md) |
| Citation fabrication / publication workaround | [Citation](reproductions/citation-fabrication/README.md) | [Seven-model results](reproductions/citation-fabrication/README.md#results) |

Portal and wiki use the same [shared runtime](shared/ai-collusion/README.md).
Each Python project has its own dependency environment; run commands from its directory.
Start with an offline preview or mock run before configuring live model calls.

## Extend an environment

1. Fork and clone the repository, then follow the study's setup instructions.
2. Run its documented checks and save the baseline configuration.
3. Create a branch; change scenario files, prompts, task specs, or model configuration.
4. Save your commit ID, resolved configuration, provider/model IDs, and result denominators.
   Distinguish supplied history, generated behavior, attempted actions, and confirmed outcomes.
5. Keep credentials in local environment variables. Generated runs and local configs are ignored.

Historical aggregate results do not imply that current provider models or live websites
will produce identical outputs. Raw private run archives are not bundled. Published
judgment excerpts are selected evidence, not complete trajectories. The wiki dump is
fetched from its public source and checked against pinned hashes.

## Release scope

This is a curated source release with a fresh history. It includes experiment code,
prompts, necessary fixtures, relevant tests, and reviewed result summaries. Internal
chats, planning/handoff archives, unrelated experiments, private infrastructure,
credentials, transcript-upload metadata, and inventories of omitted files are excluded.

[Publication notes](PUBLICATION.md) describe edits and evidence limits.
[Validation](VALIDATION.md) records checks and [SHA256SUMS.json](SHA256SUMS.json)
records released file hashes. Verify them with `python3 scripts/verify_release.py`.

**License pending:** no open-source license has been granted yet. Public visibility
and GitHub forking do not grant unrestricted reuse or redistribution rights.
Third-party materials retain their original terms; see [third-party notices](THIRD_PARTY.md).
