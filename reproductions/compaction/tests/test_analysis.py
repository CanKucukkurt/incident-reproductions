import math

from compaction_reader.score.analysis import rate_table, wilson


def test_wilson_known_values():
    p, lo, hi = wilson(0, 10)
    assert p == 0 and lo == 0 and abs(hi - 0.2775) < 1e-3
    p, lo, hi = wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_rate_table_groups():
    rows = [
        {"model": "m", "instruction": "conceal", "disclosure": "none", "non_disclosure": True},
        {"model": "m", "instruction": "conceal", "disclosure": "full", "non_disclosure": False},
        {"model": "m", "instruction": "none", "disclosure": "hedged", "non_disclosure": False},
    ]
    t = rate_table(rows, ("model", "instruction"))
    assert [(r["instruction"], r["n"], r["rate"]) for r in t] == [("conceal", 2, 0.5), ("none", 1, 0.0)]
    assert t[1]["hedged"] == 1
