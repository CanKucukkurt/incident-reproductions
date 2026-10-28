"""Experiment cells: the core grid (base cell x instruction) plus one-at-a-time
sweeps from the base cell, each crossed with the instruction values."""

from __future__ import annotations

from typing import Any

from ..config import BaseCell, RunConfig
from ..errors import ConfigError

CONDITION_FIELDS = ("instruction", "claimed_author", "summary_role", "compaction_variant", "mode", "insert_position", "edit_mode")


def cell_label(cell: BaseCell) -> str:
    return "|".join(f"{k}={getattr(cell, k)}" for k in CONDITION_FIELDS)


def expand_cells(cfg: RunConfig) -> list[BaseCell]:
    grid_fields = set(cfg.grid)
    unknown = (grid_fields | set(cfg.sweeps)) - set(CONDITION_FIELDS)
    if unknown:
        raise ConfigError(f"unknown condition fields: {sorted(unknown)}; known: {CONDITION_FIELDS}")
    base = cfg.base.model_dump()
    cells: list[BaseCell] = []
    seen: set[str] = set()

    def add(d: dict[str, Any]) -> None:
        c = BaseCell.model_validate(d)
        lab = cell_label(c)
        if lab not in seen:
            seen.add(lab)
            cells.append(c)

    # core grid: cartesian product over grid fields, other fields at base
    combos: list[dict[str, Any]] = [dict(base)]
    for field_name, values in cfg.grid.items():
        combos = [{**c, field_name: v} for c in combos for v in values]
    for c in combos:
        add(c)

    # sweeps: one field changed at a time, crossed with the instruction values
    instr_values = cfg.sweep_instructions or cfg.grid.get("instruction", [base["instruction"]])
    for field_name, values in cfg.sweeps.items():
        for v in values:
            for instr in instr_values:
                add({**base, field_name: v, "instruction": instr})
    return cells


def sweep_field_of(cell: BaseCell, base: BaseCell) -> str:
    """Name the field (other than instruction) that differs from base, or 'base'."""
    diffs = [k for k in CONDITION_FIELDS if k != "instruction" and getattr(cell, k) != getattr(base, k)]
    return diffs[0] if diffs else "base"
