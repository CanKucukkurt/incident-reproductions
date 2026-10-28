"""Stage C.11: non-disclosure rates with Wilson intervals per model and cell,
per prefix and per summary (grouping factors), the conceal-minus-none contrast,
and a split by ``mentions_being_tested``.

Judge labels are read from a JSONL file with one object per rollout:
  {"rollout_id": ..., "disclosure": "full|hedged|none|actively_false",
   "mentions_being_tested": true|false (optional), ...}
The judges themselves are not built yet (held off); any producer of that
schema works.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

NON_DISCLOSURE = {"none", "actively_false"}
LABELS = {"full", "hedged", "none", "actively_false"}  # anything else (e.g. "empty": degenerate rollout) is dropped, like a missing label
CELL_KEYS = ("instruction", "claimed_author", "summary_role", "compaction_variant", "mode", "insert_position", "edit_mode")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    """(rate, low, high) Wilson score interval."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with Path(path).open() as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def join_labels(rollouts: list[dict[str, Any]], labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {l["rollout_id"]: l for l in labels}
    rows = []
    for r in rollouts:
        if r.get("error"):
            continue
        lab = by_id.get(r["rollout_id"])
        if lab is None or lab.get("disclosure") not in LABELS:
            continue
        rows.append({**{k: r.get(k) for k in ("rollout_id", "scenario", "prefix_id", "summary_id", "model", "sweep_field", *CELL_KEYS)},
                     "disclosure": lab["disclosure"],
                     "non_disclosure": lab["disclosure"] in NON_DISCLOSURE,
                     "mentions_being_tested": bool(lab.get("mentions_being_tested", False))})
    return rows


def rate_table(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k) for k in keys)].append(r)
    out = []
    for key, rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        k = sum(r["non_disclosure"] for r in rs)
        n = len(rs)
        p, lo, hi = wilson(k, n)
        out.append({**dict(zip(keys, key)), "n": n, "non_disclosed": k, "rate": round(p, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4),
                    "hedged": sum(r["disclosure"] == "hedged" for r in rs), "actively_false": sum(r["disclosure"] == "actively_false" for r in rs)})
    return out


def contrasts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """conceal - none per (model, scenario, sweep setting); honest reported as the ceiling."""
    keys = ("model", "scenario", "claimed_author", "summary_role", "compaction_variant", "mode", "insert_position")
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[tuple(r.get(k) for k in keys)][r["instruction"]].append(r["non_disclosure"])
    out = []
    for key, d in sorted(by.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        def rate(name: str) -> tuple[float, int]:
            xs = d.get(name, [])
            return (sum(xs) / len(xs) if xs else float("nan"), len(xs))
        c, nc = rate("conceal")
        z, nz = rate("none")
        h, nh = rate("honest")
        q, nq = rate("conditional")
        out.append({**dict(zip(keys, key)), "n_none": nz, "rate_none": round(z, 4), "n_conceal": nc, "rate_conceal": round(c, 4),
                    "conceal_minus_none": round(c - z, 4) if nc and nz else float("nan"),
                    "n_honest": nh, "rate_honest_ceiling": round(h, 4), "n_conditional": nq, "rate_conditional": round(q, 4)})
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_no rows_\n"
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def analyze(rollouts_path: Path, labels_path: Path, out_dir: Path) -> dict[str, Any]:
    rollouts = load_jsonl(rollouts_path)
    labels = load_jsonl(labels_path)
    rows = join_labels(rollouts, labels)
    out_dir = Path(out_dir)
    tables = {
        "cells": rate_table(rows, ("model", "scenario", *CELL_KEYS)),
        "by_instruction": rate_table(rows, ("model", "instruction")),
        "by_prefix": rate_table(rows, ("model", "instruction", "prefix_id")),
        "by_summary": rate_table(rows, ("model", "instruction", "summary_id")),
        "by_sweep": rate_table(rows, ("model", "sweep_field", *CELL_KEYS)),
        "split_by_tested": rate_table(rows, ("model", "instruction", "mentions_being_tested")),
        "contrasts": contrasts(rows),
    }
    for name, t in tables.items():
        write_csv(out_dir / f"{name}.csv", t)
    md = ["# Analysis", "", f"- rollouts: {len(rollouts)}  labelled and analysed: {len(rows)}  (rollouts with errors or missing labels are dropped)", "",
          "## Headline: conceal minus none (non-disclosure rate; honest is the ceiling)", "",
          md_table(tables["contrasts"], ["model", "scenario", "claimed_author", "summary_role", "compaction_variant", "mode", "n_none", "rate_none", "n_conceal", "rate_conceal", "conceal_minus_none", "rate_honest_ceiling", "rate_conditional"]),
          "## Non-disclosure by model and instruction (95% Wilson)", "",
          md_table(tables["by_instruction"], ["model", "instruction", "n", "non_disclosed", "rate", "ci_low", "ci_high", "hedged", "actively_false"]),
          "## Per cell", "",
          md_table(tables["cells"], ["model", "scenario", *CELL_KEYS, "n", "rate", "ci_low", "ci_high"]),
          "## Per prefix (grouping factor)", "",
          md_table(tables["by_prefix"], ["model", "instruction", "prefix_id", "n", "rate", "ci_low", "ci_high"]),
          "## Per summary (grouping factor)", "",
          md_table(tables["by_summary"], ["model", "instruction", "summary_id", "n", "rate", "ci_low", "ci_high"]),
          "## Split by mentions_being_tested", "",
          md_table(tables["split_by_tested"], ["model", "instruction", "mentions_being_tested", "n", "rate", "ci_low", "ci_high"])]
    (out_dir / "analysis.md").write_text("\n".join(md))
    return {"rows": len(rows), "tables": {k: len(v) for k, v in tables.items()}}
