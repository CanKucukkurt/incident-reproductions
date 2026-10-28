from compaction_reader.config import RunConfig
from compaction_reader.run.conditions import cell_label, expand_cells, sweep_field_of


def test_grid_and_sweeps():
    cfg = RunConfig(run_name="r", prepare_run="p", grid={"instruction": ["none", "conceal"]},
                    sweeps={"claimed_author": ["other_model", "human_engineer"], "mode": ["final_answer_only"]})
    cells = expand_cells(cfg)
    labels = [cell_label(c) for c in cells]
    assert len(labels) == len(set(labels))
    assert len(cells) == 2 + 3 * 2
    assert [sweep_field_of(c, cfg.base) for c in cells] == ["base", "base", "claimed_author"] * 1 + ["claimed_author"] * 3 + ["mode"] * 2
    assert all(c.instruction in ("none", "conceal") for c in cells)


def test_sweep_instructions_override():
    cfg = RunConfig(run_name="r", prepare_run="p", sweeps={"summary_role": ["assistant"]}, sweep_instructions=["conceal"])
    cells = expand_cells(cfg)
    assert sum(c.summary_role == "assistant" for c in cells) == 1
