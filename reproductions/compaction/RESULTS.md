# Compaction: exploratory results

Two local DeepSeek V4.1 Flash analysis snapshots are included in `results/`.
Each analyzed 47 labeled rollouts out of 64 total; errored or unlabeled rollouts were
excluded by the historical analysis. Each snapshot reports zero non-disclosures among
its analyzed rollouts. Per-instruction sample sizes are 11 or 12, with wide uncertainty:
the 95% Wilson upper bound is about 24% for 12 observations and 26% for 11.

These snapshots are exploratory, selected analyses rather than a complete study.
They use the `claude_code` compaction profile with DeepSeek, so they do not satisfy
the current model-to-native-harness matching policy documented in this environment.
They must not be presented as a native DeepSeek compaction result or as evidence of
no concealment risk. The snapshots may share source prefixes; do not pool them as
independent trials. Missing labels and run failures can bias the analyzed subset.

See `by_instruction.csv`, `cells.csv`, and `contrasts.csv` in each snapshot directory.
The raw rollouts, judging inputs, and full run configurations are not released here;
these historical aggregates cannot be independently regenerated from this export.
For new experiments, follow the README and keep the acting model and harness
consistent throughout each trajectory.
