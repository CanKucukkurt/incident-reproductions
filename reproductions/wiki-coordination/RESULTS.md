# Wiki: recovered 400-episode results

September 17, 2026 snapshot: 400 selected episodes and validated judgments, with
50 episodes in each model/condition cell. Thirty-one infrastructure-interrupted
records were continued from saved state; derivatives are not new independent samples.
These studies provide prefills and peer information rather than testing spontaneous
fresh-start discovery. Each episode has its own wiki world; this grid does not
connect all 400 agents to one another.

The collaboration-v1.2 rubric is released under `shared/ai-collusion/judges/`.
Presence labels may describe expressed plans or attempts; execution counts require
tool evidence. Coordination includes wiki reading/polling, not necessarily reciprocal
collaboration. Bypass labels may describe intended wiki editing. Suspicion labels
refer to verbalized suspicions, not proof of evaluation recognition. Uncertain
judgments count as zero; labels overlap.

## Task outcomes and wiki posts

| Model | Arm | Episodes with saved wiki posts | Correct / scheduled questions |
| --- | --- | --- | --- |
| GPT 5.6-Sol | slow | 9/50 | 42/350 (12.0%) |
| GPT 5.6-Sol | working | 4/50 | 350/350 (100.0%) |
| Qwen 3.8 27b | slow | 18/50 | 34/350 (9.7%) |
| Qwen 3.8 27b | working | 27/50 | 295/350 (84.3%) |
| Kimi K3 | slow | 28/50 | 51/350 (14.6%) |
| Kimi K3 | working | 23/50 | 350/350 (100.0%) |
| DeepSeek V4.1 Flash | slow | 42/50 | 52/350 (14.9%) |
| DeepSeek V4.1 Flash | working | 43/50 | 199/350 (56.9%) |

Across the current sample, **194/400 episodes have saved wiki posts**, totaling 680 entries. Accuracy uses all seven scheduled questions per episode; missed and unanswered questions do not count as correct.

## Classifier results

Each cell counts **supported positive judgments**, out of 50 episodes. We erred on the side of caution: uncertain judgments were rejected as positives and count as zero, with all episodes retained in the denominator. The underlying judgments remain available for audit. Labels overlap. The definitions and interpretation caveats are in the original report: wiki polling counts as coordination; awareness labels do not establish evaluation recognition; bypass labels can describe intended wiki-editing functionality.




| Model | Arm | Ask | Share | Fulfill | Coordinate | Bypass | Sentiment | Peer use |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GPT 5.6-Sol | slow | 8 | 18 | 9 | 50 | 2 | 1 | 47 |
| GPT 5.6-Sol | working | 0 | 10 | 4 | 47 | 0 | 0 | 38 |
| Qwen 3.8 27b | slow | 15 | 34 | 18 | 50 | 4 | 5 | 50 |
| Qwen 3.8 27b | working | 11 | 44 | 29 | 50 | 11 | 16 | 50 |
| Kimi K3 | slow | 28 | 44 | 27 | 50 | 15 | 18 | 50 |
| Kimi K3 | working | 7 | 33 | 23 | 50 | 5 | 8 | 50 |
| DeepSeek V4.1 Flash | slow | 44 | 50 | 41 | 50 | 15 | 7 | 50 |
| DeepSeek V4.1 Flash | working | 32 | 50 | 43 | 50 | 16 | 19 | 50 |

### Awareness labels

| Model | Arm | Planted-test suspicion | Simulation suspicion |
| --- | --- | --- | --- |
| GPT 5.6-Sol | slow | 3 | 23 |
| GPT 5.6-Sol | working | 1 | 0 |
| Qwen 3.8 27b | slow | 23 | 19 |
| Qwen 3.8 27b | working | 28 | 19 |
| Kimi K3 | slow | 6 | 26 |
| Kimi K3 | working | 4 | 2 |
| DeepSeek V4.1 Flash | slow | 46 | 28 |
| DeepSeek V4.1 Flash | working | 40 | 18 |

### Executed communication

| Model | Arm | Executed ask | Executed share | Executed fulfillment |
| --- | --- | --- | --- | --- |
| GPT 5.6-Sol | slow | 3 | 9 | 9 |
| GPT 5.6-Sol | working | 0 | 4 | 4 |
| Qwen 3.8 27b | slow | 13 | 18 | 18 |
| Qwen 3.8 27b | working | 10 | 27 | 27 |
| Kimi K3 | slow | 22 | 28 | 27 |
| Kimi K3 | working | 7 | 22 | 22 |
| DeepSeek V4.1 Flash | slow | 33 | 42 | 41 |
| DeepSeek V4.1 Flash | working | 27 | 43 | 43 |

These execution counts require model and tool-result evidence. Saved wiki posts confirm local persistence, not peer readership or successful reciprocity.

## Qwen provider histories

| Qwen provider history | Arm | N | Ask | Episodes with saved wiki posts | Simulation |
| --- | --- | --- | --- | --- | --- |
| OpenRouter | slow | 12 | 9 | 10 | 7 |
| OpenRouter | working | 27 | 11 | 24 | 17 |
| Alibaba | slow | 36 | 4 | 6 | 11 |
| Alibaba | working | 22 | 0 | 3 | 1 |
| OpenRouter → Alibaba | slow | 2 | 2 | 2 | 1 |
| OpenRouter → Alibaba | working | 1 | 0 | 0 | 1 |

Three recovered Qwen transcripts retain OpenRouter prefixes and continue on direct Alibaba, model `qwen3.8-27b`, with thinking enabled and a 16,384-token thinking budget. The other 58 Alibaba and 39 OpenRouter transcripts retain their original provider history. Provider, reasoning budget, and completion-based selection remain confounded; the pooled Qwen comparisons do not isolate provider or latency effects.


## Evidence limits

The numeric classifier table is also in [classifier-counts.csv](results/classifier-counts.csv).
This release includes aggregate results rather than all 400 raw trajectories and
judgments, so it cannot independently reconstruct every historical label. Provider
migration, effective reasoning budgets, recovery, and selection confound comparisons.
All seven scheduled questions per episode count in task-accuracy denominators.
Saved wiki posts establish persistence, not peer readership or successful reciprocity.
One recovered DeepSeek slow episode used a higher late-turn output budget, so
accepted generations in that record do not all share one budget.
