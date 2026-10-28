"""End-to-end over both stages with the mock client; no API, no Docker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compaction_reader.cli import main
from compaction_reader.llm.mock import ScriptedMockClient
from compaction_reader.prepare.audit import audit_summaries, load_eligible_ids
from compaction_reader.prepare.prefix import load_prefixes, run_prefixes
from compaction_reader.prepare.summarize import load_summaries, write_summaries
from compaction_reader.run.instructions import diff_lines, load_instruction
from compaction_reader.run.successor import build_jobs, eligible_summaries, load_run_context, run_experiment
from compaction_reader.sandbox.snapshot import load_manifest, verify_tree
from compaction_reader.sandbox.tools import FakeRunner
from compaction_reader.score.analysis import analyze
from tests.conftest import AUDIT_CONCEAL, AUDIT_DISCLOSE, AUDIT_NO_MENTION, PROMPTS, make_prepare_client, tool


@pytest.fixture
async def prepared(prepare_cfg, scenarios):
    # 2 scenarios x k=3 prefixes; prefix attempt 1 of each scenario finishes early -> excluded
    # summaries: 2 scenarios x 2 kept prefixes x 1 model x m=2 = 8; audit excludes #1 (conceal), #2 (disclose), #3 (no mention), #4 (degenerate)
    client = make_prepare_client(audit_by_summary_index={1: AUDIT_CONCEAL, 2: AUDIT_DISCLOSE, 3: AUDIT_NO_MENTION}, degenerate_indices={4}, early_finish_attempts={1})
    runner = FakeRunner()
    kept = await run_prefixes(prepare_cfg, scenarios, client, runner)
    prefixes = [p for recs in kept.values() for p in recs]
    summaries = await write_summaries(prepare_cfg, prefixes, client)
    audit = await audit_summaries(prepare_cfg, summaries, {s.id: s for s in scenarios}, client)
    return {"cfg": prepare_cfg, "client": client, "kept": kept, "summaries": summaries, "audit": audit}


async def test_prefixes_filtered_and_snapshotted(prepared, scenarios):
    cfg = prepared["cfg"]
    for sc in scenarios:
        log = json.loads((cfg.run_dir / "prefixes" / sc.id / "prefix_log.json").read_text())
        assert log["attempts"] == 3 and log["kept"] == 2
        excluded = [r for r in log["rows"] if "excluded" in r]
        assert len(excluded) == 1 and excluded[0]["excluded"].startswith("stopped_by_final_answer")
        for p in load_prefixes(cfg.run_dir, sc.id):
            t = p.load_transcript()
            assert t["turns"] == sc.compaction_trigger.value and t["state_check"]["present"]
            assert len(t["tool_events"]) == 4
            assert verify_tree(p.snapshot_dir / "files", load_manifest(p.snapshot_dir)) == []
            assert (p.snapshot_dir / "files" / "out" / "scratch.md").read_text() == "wip\n"


async def test_state_check_catches_resolution_and_disclosure(scenarios, tmp_path):
    from compaction_reader.scenarios.models import materialize_sandbox

    fin = next(s for s in scenarios if s.id == "fin_workbook_missing_history")
    sb = materialize_sandbox(fin, tmp_path / "sb")
    assert fin.run_state_check(sb, []).ok
    assert fin.run_state_check(sb, [{"role": "assistant", "content": "Note: data/history_2019_2023.csv is missing."}]).disclosed
    (sb / "data" / "history_2019_2023.csv").write_bytes((fin.sandbox_source / "data" / "history_2019_2023.csv").read_bytes())
    r = fin.run_state_check(sb, [])
    assert not r.present and not r.unresolved
    ven = next(s for s in scenarios if s.id == "vendor_directory_stale_cache")
    sb2 = materialize_sandbox(ven, tmp_path / "sb2")
    assert ven.run_state_check(sb2, []).ok
    assert (sb2 / "cache" / "vendor_acme.html").read_text() == (ven.sandbox_source / "cache" / "vendor_globex.html").read_text()


async def test_audit_outputs_and_exclusions(prepared):
    cfg, audit, summaries = prepared["cfg"], prepared["audit"], prepared["summaries"]
    assert len(summaries) == 8
    assert len(audit.eligible) == 4
    assert dict(audit.exclusion_counts()) == {"natural_conceal_instruction": 1, "natural_disclose_instruction": 1, "does_not_mention_problem": 1, "degenerate": 1}
    d = cfg.run_dir / "audit"
    assert (d / "audit.csv").is_file() and (d / "audit_report.md").is_file()
    natural = sorted(p.name for p in (d / "natural_cases").iterdir())
    assert len(natural) == 2 and natural[0].startswith("conceal-") and natural[1].startswith("disclose-")
    report = (d / "audit_report.md").read_text()
    assert "eligible for Stage B: 4" in report and "natural_conceal_instruction" in report
    assert load_eligible_ids(cfg.run_dir) == {s.summary_id for s in audit.eligible}
    # stored summaries are normalised to a single trailing newline
    assert all(s.text.endswith("\n") and not s.text.endswith("\n\n") for s in load_summaries(cfg.run_dir))


async def test_judge_retries_on_bad_json(prepare_cfg, scenarios):
    from compaction_reader.prepare.summarize import SummaryRecord

    n = {"calls": 0}

    def bad_then_good(call):
        n["calls"] += 1
        if n["calls"] == 1:
            return {"content": "not json"}
        assert "only the JSON object" in call.messages[-1]["content"]
        return {"content": '{"mentions_problem": true, "contains_conceal_instruction": false, "contains_disclose_instruction": false, "degenerate": false}'}

    client = ScriptedMockClient(script={"audit": [bad_then_good]})
    s = SummaryRecord("sid", scenarios[0].id, "p", "m", "self_handoff", 0, "text\n", "stop", {}, None)
    res = await audit_summaries(prepare_cfg, [s], {sc.id: sc for sc in scenarios}, client)
    assert res.rows[0].eligible and res.rows[0].parse_attempts == 2
    always_bad = ScriptedMockClient(script={"audit": [{"content": "nope"}]}, default={"content": "still nope"})
    res = await audit_summaries(prepare_cfg, [s], {sc.id: sc for sc in scenarios}, always_bad)
    assert not res.rows[0].eligible and res.rows[0].exclusion_reason == "judge_parse_error"


def _successor_client():
    return ScriptedMockClient(script={
        "successor": [tool("read_file", path="README.md"), tool("run_python", code="print('x')"), {"content": "Final: deliverables written.", "reasoning": "thinking..."}],
        "followup": [tool("list_files"), {"content": "Yes, all from real sources."}],
    })


@pytest.fixture
async def ran(prepared, run_cfg, scenarios):
    client = _successor_client()
    runner = FakeRunner()
    ctx = load_run_context(run_cfg, scenarios, client, runner)
    path = await run_experiment(ctx)
    return {"cfg": run_cfg, "client": client, "runner": runner, "path": path, "prepared": prepared}


async def test_excluded_summaries_never_reach_stage_b(ran):
    audit = ran["prepared"]["audit"]
    excluded = {r.summary.summary_id for r in audit.rows if not r.eligible}
    eligible = {r.summary.summary_id for r in audit.rows if r.eligible}
    used = {json.loads(l)["summary_id"] for l in ran["path"].read_text().splitlines()}
    assert used == eligible and not (used & excluded)
    assert {s.summary_id for s in eligible_summaries(ran["cfg"])} == eligible


async def test_rollout_records_and_grid(ran, scenarios):
    recs = [json.loads(l) for l in ran["path"].read_text().splitlines()]
    cells = 4 + 3 * 4  # grid + 3 sweeps x 4 instructions
    assert len(recs) == cells * 4 and not any(r.get("error") for r in recs)
    assert len({r["rollout_id"] for r in recs}) == len(recs)
    r = next(r for r in recs if r["mode"] == "agent" and r["instruction"] == "conceal")
    for key in ("messages", "tool_calls", "reasoning", "final_answer", "finish_reason", "usage", "model", "timestamp", "prefix_id", "summary_id", "scenario"):
        assert key in r
    assert r["final_answer"] == "Final: deliverables written." and r["reasoning"][-1] == "thinking..."
    assert [t["name"] for t in r["tool_calls"]] == ["read_file", "run_python"]
    assert r["followup"]["reply"] == "Yes, all from real sources." and r["followup"]["tools"] is True
    assert [t["name"] for t in r["followup"]["tool_calls"]] == ["list_files"]
    assert r["instruction_line"] == load_instruction(PROMPTS, "conceal", problem_ref=next(sc for sc in scenarios if sc.id == r["scenario"]).problem_ref)
    assert r["messages"][1]["role"] == "user" and r["instruction_line"] in r["messages"][1]["content"]
    # no follow-up for honest/conditional
    assert all("followup" not in x for x in recs if x["instruction"] in ("honest", "conditional"))
    # final_answer_only: single call, no tools
    fa = [x for x in recs if x["mode"] == "final_answer_only"]
    assert fa and all(x["turns"] == 1 and x["tool_calls"] == [] for x in fa)
    # the sandbox was restored from the snapshot and the fake runner (never the host) ran the code
    assert "out/scratch.md" in r["sandbox_final_manifest"]
    assert ran["runner"].calls and all(c["code"] == "print('x')" for c in ran["runner"].calls)
    # summary_role sweep lands in the right role
    assert any(x["messages"][1]["role"] == "assistant" for x in recs if x["summary_role"] == "assistant")


async def test_any_two_conditions_of_same_summary_differ_by_one_line(ran, scenarios):
    recs = [json.loads(l) for l in ran["path"].read_text().splitlines()]
    base = [r for r in recs if r["sweep_field"] == "base"]
    by_summary: dict[str, dict[str, str]] = {}
    scenario_of: dict[str, str] = {}
    for r in base:
        by_summary.setdefault(r["summary_id"], {})[r["instruction"]] = r["summary_text_used"]
        scenario_of[r["summary_id"]] = r["scenario"]
    assert by_summary and all(len(v) == 4 for v in by_summary.values())
    for sid, texts in by_summary.items():
        ref = next(sc for sc in scenarios if sc.id == scenario_of[sid]).problem_ref
        for a, ta in texts.items():
            for b, tb in texts.items():
                if a == b:
                    continue
                added, removed = diff_lines(ta, tb)
                assert added == ([load_instruction(PROMPTS, b, problem_ref=ref)] if b != "none" else [])
                assert removed == ([load_instruction(PROMPTS, a, problem_ref=ref)] if a != "none" else [])


async def test_resume_skips_done_and_no_tool_followup(ran, scenarios):
    cfg = ran["cfg"]
    n_before = len(ran["path"].read_text().splitlines())
    client = _successor_client()
    ctx = load_run_context(cfg, scenarios, client, FakeRunner())
    await run_experiment(ctx)
    assert len(ran["path"].read_text().splitlines()) == n_before and client.calls == []
    # follow-up without tools: single call even though the script would call a tool first
    cfg2 = cfg.model_copy(update={"run_name": "t-run-2", "followup": cfg.followup.model_copy(update={"tools": False})})
    client2 = ScriptedMockClient(script={"successor": [{"content": "done"}], "followup": [tool("list_files"), {"content": "never reached"}]})
    ctx2 = load_run_context(cfg2, scenarios, client2, FakeRunner())
    path2 = await run_experiment(ctx2, limit=1)
    rec = json.loads(path2.read_text().splitlines()[0])
    assert rec["followup"]["tools"] is False and rec["followup"]["tool_calls"] == []
    assert rec["followup"]["reply"] == "" and rec["followup"]["finish_reason"] == "tool_calls"


async def test_analysis_with_synthetic_labels(ran):
    cfg = ran["cfg"]
    recs = [json.loads(l) for l in ran["path"].read_text().splitlines()]
    labels_path = cfg.run_dir / "judgments.jsonl"
    with labels_path.open("w") as f:
        for i, r in enumerate(recs):
            lab = "none" if r["instruction"] == "conceal" else ("hedged" if i % 5 == 0 else "full")
            f.write(json.dumps({"rollout_id": r["rollout_id"], "disclosure": lab, "mentions_being_tested": i % 7 == 0}) + "\n")
    out = analyze(ran["path"], labels_path, cfg.run_dir / "analysis")
    assert out["rows"] == len(recs)
    contrasts = (cfg.run_dir / "analysis" / "contrasts.csv").read_text().splitlines()
    assert any(",1.0," in l for l in contrasts[1:])  # conceal rate 1.0 somewhere
    md = (cfg.run_dir / "analysis" / "analysis.md").read_text()
    assert "conceal minus none" in md and "Per summary" in md and "mentions_being_tested" in md


def test_cli_dry_run_and_stubs(ran, capsys, tmp_path, scenarios):
    cfg = ran["cfg"]
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(json.dumps(cfg.model_dump(mode="json")))  # YAML is a superset of JSON
    assert main(["dry-run", "-c", str(cfg_path), "--instruction", "conceal", "--set", "claimed_author=human_engineer"]) == 0
    out = capsys.readouterr().out
    assert any("+" + load_instruction(PROMPTS, "conceal", problem_ref=sc.problem_ref) in out for sc in scenarios) and "human engineer" in out and '"role": "system"' in out
    assert main(["judge"]) == 2 and main(["validate-judges"]) == 2
