"""Re-fork an EXISTING codex-native run at an earlier point of its task turn and compact there.

Codex's `thread/fork` copies the parent's rollout file (`forked_from_ordinal_exclusive`). So a mid-turn cut of a
finished trajectory needs no re-run: copy the source run's rollout into a fresh per-run Codex home, truncated after a
chosen ordinal (or after the N-th tool call), fork that thread by id inside the task container, `thread/compact/start`
on each fork, decrypt the blob (Responses API sandwich), and write summaries.jsonl / task.json / task.thread.json in the
same layout as scripts/codex_native_writer.py (so writer_judge.py --jsonl and codex_native_to_docent.py work as is).

    .venv/bin/python scripts/codex_native_refork.py --source-run tele-smoke-t1 --run tele-smoke-t1-cut16 \
        --cut-after-command 16 --forks 1 --container compaction-codex:teleport-3587cca-native \
        --container-opt=--security-opt=seccomp=unconfined --container-opt=--security-opt=apparmor=unconfined --container-opt=--cap-add=SYS_ADMIN

Only the compaction and the decrypt call the API; the workspace state in the container is irrelevant to compaction
(the fork never runs a command), so the image is started at its base state.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.codex_native_writer as w  # noqa: E402
from scripts.codex_native_writer import CodexRPC, compact, container_argv, decrypt_blob, eprint, extract_compaction_blob, load_openai_key, prepare_codex_home, read_thread, wait_response  # noqa: E402


def load_rollout(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def describe(row: dict) -> str:
    p = row.get("payload") or {}
    t = row.get("type"); pt = p.get("type") if isinstance(p, dict) else None
    if pt == "custom_tool_call":
        return f"call {p.get('name')}: {(p.get('input') or '')[:100]!r}"
    if pt == "custom_tool_call_output":
        return "tool output"
    if pt == "message":
        return f"message[{p.get('role')}]: {''.join(c.get('text', '') for c in p.get('content', []) if isinstance(c, dict))[:80]!r}"
    return f"{t}/{pt}"


def cut_ordinal_after_command(rows: list[dict], n: int) -> int:
    """Ordinal of the last row belonging to the N-th tool call (its output + trailing events, before the next response_item)."""
    calls = [i for i, r in enumerate(rows) if r.get("type") == "response_item" and (r.get("payload") or {}).get("type") == "custom_tool_call"]
    if n < 1 or n > len(calls):
        raise SystemExit(f"--cut-after-command {n}: rollout has {len(calls)} tool calls")
    i = calls[n - 1]
    # walk to this call's output, then past trailing event rows
    j = i + 1
    while j < len(rows) and (rows[j].get("payload") or {}).get("type") != "custom_tool_call_output":
        j += 1
    if j >= len(rows):
        raise SystemExit(f"tool call #{n} has no output row")
    k = j + 1
    while k < len(rows) and rows[k].get("type") != "response_item":
        k += 1
    return rows[k - 1]["ordinal"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-run", required=True, help="finished run under runs/codex-native (its codex-home/sessions holds the task rollout)")
    ap.add_argument("--run", required=True, help="new run name under runs/codex-native")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cut-ordinal", type=int, help="keep rollout rows with ordinal <= this")
    g.add_argument("--cut-after-command", type=int, help="keep everything through the N-th tool call and its output")
    ap.add_argument("--container", required=True, metavar="IMAGE")
    ap.add_argument("--container-workdir", default="/app")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--container-env", action="append", default=[])
    ap.add_argument("--container-opt", action="append", default=[])
    ap.add_argument("--forks", type=int, default=1)
    ap.add_argument("--fork-model", default=None, help="default: the source task's model (one model per trajectory)")
    ap.add_argument("--decrypt-repeats", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="overwrite an existing run dir")
    ap.add_argument("--append", action="store_true", help="add forks to an existing run dir (same cut; fork numbering continues)")
    args = ap.parse_args(argv)

    root = REPO_ROOT / "runs" / "codex-native"
    src = root / args.source_run
    task = json.loads((src / "task.json").read_text())
    thread_id = task["thread_id"]
    hits = sorted((src / "codex-home" / "sessions").rglob(f"*{thread_id}*.jsonl"))
    if not hits:
        raise SystemExit(f"no rollout for thread {thread_id} under {src}/codex-home/sessions")
    src_rollout = hits[-1]
    rows = load_rollout(src_rollout)
    cut = args.cut_ordinal if args.cut_ordinal is not None else cut_ordinal_after_command(rows, args.cut_after_command)
    kept = [r for r in rows if r.get("ordinal") is None or r["ordinal"] <= cut]
    dropped = len(rows) - len(kept)
    if dropped == 0:
        raise SystemExit(f"cut ordinal {cut} keeps the whole rollout ({len(rows)} rows)")
    n_calls = sum(1 for r in kept if (r.get("payload") or {}).get("type") == "custom_tool_call")

    out = root / args.run
    forks = []
    if out.exists() and args.append:
        prev = json.loads((out / "cut.json").read_text())
        if prev["cut_ordinal"] != cut:
            raise SystemExit(f"--append: existing run was cut at ordinal {prev['cut_ordinal']}, requested {cut}")
        if (out / "forks.json").is_file():
            forks = json.loads((out / "forks.json").read_text())
        eprint(f"append: {len(forks)} existing fork(s) in {out}")
    elif out.exists():
        if not args.force:
            raise SystemExit(f"{out} exists (use --force or --append)")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    home = prepare_codex_home(out / "codex-home")
    rel = src_rollout.relative_to(src / "codex-home")
    dst_rollout = home / rel
    dst_rollout.parent.mkdir(parents=True, exist_ok=True)
    if not dst_rollout.exists():
        dst_rollout.write_text("\n".join(json.dumps(r) for r in kept) + "\n")
    cut_info = {"source_run": args.source_run, "source_rollout": str(src_rollout), "cut_ordinal": cut, "rows_kept": len(kept), "rows_dropped": dropped,
                "tool_calls_kept": n_calls, "last_rows": [describe(r) for r in kept[-6:]], "first_dropped": [describe(r) for r in rows[len(kept):len(kept) + 4]]}
    (out / "cut.json").write_text(json.dumps(cut_info, indent=1))
    eprint(f"cut at ordinal {cut}: kept {len(kept)} rows ({n_calls} tool calls), dropped {dropped}")
    for d in cut_info["last_rows"]:
        eprint(f"   kept: {d}")
    for d in cut_info["first_dropped"]:
        eprint("dropped: " + d)

    w.CODEX_HOME = home
    cname = f"cr-{args.run}"
    argv_rpc = container_argv(args.container, cname, home, args.container_workdir, args.platform, args.container_env, args.container_opt)
    eprint(f"container mode: {' '.join(argv_rpc)}")
    rpc = CodexRPC(out / "rpc.log.jsonl", argv_rpc)
    fork_model = args.fork_model or task.get("model")
    scenario_id = task.get("scenario_id")
    new_task = {**{k: v for k, v in task.items() if k != "final_message"}, "refork_of": args.source_run, "cut": f"ordinal<={cut} (tool calls {n_calls})", "commands": n_calls,
                "status": "cut_mid_turn", "final_message": None, "source_final_message": task.get("final_message")}
    (out / "task.json").write_text(json.dumps(new_task, indent=1))
    start = len(forks)
    try:
        rid = rpc.send("initialize", {"clientInfo": {"name": "compaction-reader", "version": "0.1"}})
        wait_response(rpc, rid); rpc.send("initialized", notify=True)
        for k in range(start, start + args.forks):
            params = {"threadId": thread_id}
            if fork_model:
                params["model"] = fork_model
            rid = rpc.send("thread/fork", params)
            res, _ = wait_response(rpc, rid, timeout=120)
            fid = res["thread"]["id"]
            fdir = out / "forks" / f"f{k:02d}"; fdir.mkdir(parents=True, exist_ok=True)
            eprint(f"fork {k}: {fid} model={res.get('model')}")
            before = read_thread(rpc, fid)
            (fdir / "thread_before_compact.json").write_text(json.dumps(before, indent=1)[:5_000_000])
            if k == 0:
                (out / "task.thread.json").write_text(json.dumps(before, indent=1)[:5_000_000])
            n_items = sum(len(t.get("items", [])) for t in (before.get("thread") or {}).get("turns", []))
            eprint(f"fork {k}: {n_items} items in forked history")
            events = compact(rpc, fid, timeout=900, tag=f"f{k:02d}-compact")
            (fdir / "compact.events.jsonl").write_text("\n".join(json.dumps(e)[:50000] for e in events) + "\n")
            (fdir / "thread_after_compact.json").write_text(json.dumps(read_thread(rpc, fid), indent=1)[:5_000_000])
            model = res.get("model") or fork_model
            rollout = w.find_rollout(fid); blob = extract_compaction_blob(rollout) if rollout else None
            decrypts = []
            if blob:
                (fdir / "compaction_blob.json").write_text(json.dumps(blob))
                for r in range(args.decrypt_repeats):
                    text = decrypt_blob(blob, model, load_openai_key())
                    (fdir / f"decrypt_{r}.txt").write_text(text); decrypts.append(len(text))
                    eprint(f"fork {k} decrypt {r}: {len(text)} chars")
                identical = len({(fdir / f"decrypt_{r}.txt").read_text() for r in range(args.decrypt_repeats)}) == 1 if args.decrypt_repeats > 1 else None
                with (out / "summaries.jsonl").open("a") as fh:
                    fh.write(json.dumps({"summary_id": f"{args.run}-f{k:02d}", "scenario_id": scenario_id, "prefix_id": f"{args.run}-{thread_id[-8:]}", "model": model,
                                         "compaction_variant": "openai_native", "sample_index": k, "text": (fdir / "decrypt_0.txt").read_text(),
                                         "decrypt_lengths": decrypts, "decrypts_identical": identical, "fork_thread_id": fid, "cut_ordinal": cut, "refork_of": args.source_run}) + "\n")
            else:
                eprint(f"fork {k}: no compaction blob found in rollout {rollout}")
            forks.append({"fork": k, "thread_id": fid, "model": model, "items": n_items, "blob_chars": len(blob["encrypted_content"]) if blob else 0, "decrypts": decrypts})
            (out / "forks.json").write_text(json.dumps(forks, indent=1))
        print(json.dumps({"run": args.run, "cut": cut_info, "forks": forks}, indent=1))
        return 0
    finally:
        rpc.close()


if __name__ == "__main__":
    raise SystemExit(main())
