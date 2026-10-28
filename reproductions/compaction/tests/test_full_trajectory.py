"""Run-to-completion prefixes with post-hoc cuts (mock client: 4 tool turns, then 'Done.')."""

from __future__ import annotations

import json

import pytest

from compaction_reader.config import CutRule, PrepareConfig
from compaction_reader.prepare.prefix import choose_cut_turn, load_prefixes, run_prefixes
from compaction_reader.sandbox.snapshot import load_manifest, verify_tree
from compaction_reader.sandbox.tools import FakeRunner
from tests.conftest import make_prepare_client


def test_choose_cut_turn_rules():
    ok = [True, True, True, False]  # 4 candidate turns; the last one fails the check
    assert choose_cut_turn(CutRule(rule="deepest"), ok) == (3, "ok")
    assert choose_cut_turn(CutRule(rule="fraction", value=0.5), ok) == (2, "ok")
    assert choose_cut_turn(CutRule(rule="turn", value=4), ok) == (None, "state_check_fails_at_turn_4")
    assert choose_cut_turn(CutRule(rule="turn", value=9), ok)[0] is None
    assert choose_cut_turn(CutRule(rule="deepest"), []) == (None, "no_candidate_turns")
    assert choose_cut_turn(CutRule(rule="deepest", min_turn=4), ok) == (None, "no_turn_passes_state_check")
    assert CutRule(rule="fraction", value=0.5).name == "f50" and CutRule(rule="turn", value=8).name == "t08" and CutRule().name == "deep"
    with pytest.raises(ValueError):
        CutRule(rule="fraction")


async def test_full_trajectory_prefixes_and_cuts(prepare_cfg: PrepareConfig, scenarios):
    cfg = prepare_cfg.model_copy(update={"prefix": prepare_cfg.prefix.model_copy(update={
        "k": 2, "run_to_completion": True, "cuts": [CutRule(rule="deepest"), CutRule(rule="fraction", value=0.5), CutRule(rule="turn", value=1)],
    })})
    kept = await run_prefixes(cfg, scenarios, make_prepare_client(), FakeRunner())
    for sc in scenarios:
        recs = kept[sc.id]
        assert sorted(r.prefix_id for r in recs) == sorted(f"{sc.id}-p{k:02d}-{lab}" for k in range(2) for lab in ("deep", "f50", "t01"))
        log = json.loads((cfg.run_dir / "prefixes" / sc.id / "prefix_log.json").read_text())
        row = log["rows"][0]
        assert row["stopped_by"] == "final_answer" and row["turns"] == 5 and row["candidate_turns"] == 4
        assert row["cuts"] == {"deep": {"turn": 4, "kept": True}, "f50": {"turn": 2, "kept": True}, "t01": {"turn": 1, "kept": True}}
        full = json.loads((cfg.run_dir / "prefixes_full" / sc.id / f"{sc.id}-p00" / "transcript.json").read_text())
        assert full["final_answer"].startswith("Done.") and len(full["state_check_by_turn"]) == 4 and len(full["turn_ends"]) == 5
        assert (cfg.run_dir / "prefixes_full" / sc.id / f"{sc.id}-p00" / "snapshots" / "turn_04" / "manifest.json").is_file()
        by_id = {p.prefix_id: p for p in load_prefixes(cfg.run_dir, sc.id)}
        for lab, t in (("deep", 4), ("f50", 2), ("t01", 1)):
            p = by_id[f"{sc.id}-p00-{lab}"]
            tr = p.load_transcript()
            assert tr["turns"] == t and tr["cut"]["turn"] == t and tr["cut"]["full_turns"] == 5
            assert all(e["turn"] <= t for e in tr["tool_events"]) and len(tr["tool_events"]) == t
            assert tr["messages"][-1]["role"] == "tool" and sum(m["role"] == "assistant" for m in tr["messages"]) == t
            assert "mock default final answer" not in json.dumps(tr["messages"])  # the final answer never leaks into a cut prefix
            assert verify_tree(p.snapshot_dir / "files", load_manifest(p.snapshot_dir)) == []
        # the workspace state matches the turn: scratch.md is written at turn 4 only
        assert (by_id[f"{sc.id}-p00-deep"].snapshot_dir / "files" / "out" / "scratch.md").is_file()
        assert not (by_id[f"{sc.id}-p00-f50"].snapshot_dir / "files" / "out" / "scratch.md").exists()


async def test_full_trajectory_resume_reuses_saved_trajectories(prepare_cfg: PrepareConfig, scenarios):
    cfg = prepare_cfg.model_copy(update={"prefix": prepare_cfg.prefix.model_copy(update={"k": 1, "run_to_completion": True, "cuts": [CutRule(rule="deepest")]})})
    client = make_prepare_client()
    first = await run_prefixes(cfg, scenarios, client, FakeRunner())
    n_calls = len(client.calls)
    # second run: nothing is re-run, cuts are re-materialised from prefixes_full
    second = await run_prefixes(cfg, scenarios, make_prepare_client(), FakeRunner())
    assert n_calls > 0 and {p.prefix_id for v in second.values() for p in v} == {p.prefix_id for v in first.values() for p in v}
    log = json.loads((cfg.run_dir / "prefixes" / scenarios[0].id / "prefix_log.json").read_text())
    assert log["rows"][0]["cuts"]["deep"]["kept"] is True
