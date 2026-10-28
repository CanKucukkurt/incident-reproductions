"""Reader side for codex-native runs: continue the task from a REAL compaction summary and see what the successor tells the user.

Given a re-fork/cut run (scripts/codex_native_refork.py) and one of its compacted forks, for each successor:
  1. start `codex app-server` in the task container with the cut run's Codex home mounted (it holds the fork's rollout,
     whose last record is the compaction); restore the workspace to the cut state by applying --restore-diff (a
     `after_task.diff.patch` written by codex_native_writer.snapshot_container) inside the container;
  2. thread/fork the compacted fork (the new thread's model context is the compaction summary), turn/start with
     --message (default "continue": what a user types after an interrupted turn), run to completion;
  3. record the final message, thread, container snapshot and the optional --final-check output under
     runs/codex-native/<cut-run>/successors/f<fork>/s<k>/ and append to runs/codex-native/<cut-run>/successors.jsonl.

    .venv/bin/python scripts/codex_native_successor.py --cut-run tele-names-t1-cut32 --fork 6 --successors 3 \
        --restore-diff runs/codex-native/tele-names-t1/container/after_task.diff.patch \
        --container compaction-codex:teleport-3587cca-native --container-opt=... --final-check '...'
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.codex_native_writer as w  # noqa: E402
from scripts.codex_native_writer import CodexRPC, container_argv, eprint, read_thread, run_turn, snapshot_container, wait_response  # noqa: E402


def dexec(cname: str, workdir: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-w", workdir, cname, "bash", "-lc", cmd], capture_output=True, text=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut-run", required=True, help="re-fork run under runs/codex-native whose forks were compacted")
    ap.add_argument("--fork", type=int, required=True, help="fork index whose compaction the successor starts from")
    ap.add_argument("--successors", type=int, default=1)
    ap.add_argument("--start", type=int, default=0, help="first successor index (to append)")
    ap.add_argument("--message", default="continue")
    ap.add_argument("--restore-diff", required=True, help="git diff (with untracked files) that recreates the workspace at the cut")
    ap.add_argument("--container", required=True)
    ap.add_argument("--container-workdir", default="/app")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--container-env", action="append", default=[])
    ap.add_argument("--container-opt", action="append", default=[])
    ap.add_argument("--model", default=None, help="default: the fork's model (one model per trajectory)")
    ap.add_argument("--final-check", default=None)
    ap.add_argument("--final-check-copy", action="append", default=[])
    ap.add_argument("--turn-timeout", type=float, default=2400)
    args = ap.parse_args(argv)

    root = REPO_ROOT / "runs" / "codex-native"
    crun = root / args.cut_run
    forks = json.loads((crun / "forks.json").read_text())
    fork = next(f for f in forks if f["fork"] == args.fork)
    model = args.model or fork.get("model")
    home = crun / "codex-home"
    w.CODEX_HOME = home
    diff = Path(args.restore_diff)
    summary_id = f"{args.cut_run}-f{args.fork:02d}"
    for k in range(args.start, args.start + args.successors):
        sdir = crun / "successors" / f"f{args.fork:02d}" / f"s{k:02d}"; sdir.mkdir(parents=True, exist_ok=True)
        cname = f"cr-{args.cut_run}-f{args.fork:02d}-s{k:02d}"
        argv_rpc = container_argv(args.container, cname, home, args.container_workdir, args.platform, args.container_env, args.container_opt)
        eprint(f"[s{k:02d}] container: {' '.join(argv_rpc)}")
        rpc = CodexRPC(sdir / "rpc.log.jsonl", argv_rpc)
        try:
            rid = rpc.send("initialize", {"clientInfo": {"name": "compaction-reader", "version": "0.1"}})
            wait_response(rpc, rid); rpc.send("initialized", notify=True)
            # restore the workspace to the cut state
            subprocess.run(["docker", "cp", str(diff), f"{cname}:/tmp/restore.patch"], check=True)
            r = dexec(cname, args.container_workdir, "git apply --binary --whitespace=nowarn /tmp/restore.patch && git status --porcelain=v1 -uall | wc -l")
            (sdir / "restore.txt").write_text(f"[rc={r.returncode}]\n{r.stdout}\n[stderr]\n{r.stderr}")
            if r.returncode != 0:
                raise SystemExit(f"[s{k:02d}] restore failed: {r.stderr[:500]}")
            eprint(f"[s{k:02d}] workspace restored: {r.stdout.strip()} changed paths")
            params = {"threadId": fork["thread_id"]}
            if model:
                params["model"] = model
            rid = rpc.send("thread/fork", params)
            res, _ = wait_response(rpc, rid, timeout=120)
            tid = res["thread"]["id"]
            before = read_thread(rpc, tid)
            (sdir / "thread_before.json").write_text(json.dumps(before, indent=1)[:5_000_000])
            kinds = [[it["type"] for it in t.get("items", [])] for t in (before.get("thread") or {}).get("turns", [])]
            eprint(f"[s{k:02d}] successor thread {tid} model={res.get('model')} turns={[ (x[:3], len(x)) for x in kinds ]}")
            t0 = time.time()
            status, msg, n_cmds, turn_id = run_turn(rpc, tid, args.message, cut_commands=None, timeout=args.turn_timeout, tag=f"s{k:02d}", out_dir=sdir)
            (sdir / "thread_after.json").write_text(json.dumps(read_thread(rpc, tid), indent=1)[:5_000_000])
            snapshot_container(cname, args.container_workdir, sdir / "container", "after_successor")
            check = None
            if args.final_check:
                for spec in args.final_check_copy:
                    src, dst = spec.split(":", 1)
                    subprocess.run(["docker", "cp", src, f"{cname}:{dst}"], check=True)
                r = dexec(cname, args.container_workdir, args.final_check)
                check = f"$ {args.final_check}\n[rc={r.returncode}]\n{r.stdout}\n[stderr]\n{r.stderr}"
                (sdir / "container" / "final_check.txt").write_text(check)
            rec = {"successor_id": f"{summary_id}-s{k:02d}", "summary_id": summary_id, "cut_run": args.cut_run, "fork": args.fork, "thread_id": tid, "model": res.get("model") or model,
                   "message": args.message, "status": status, "commands": n_cmds, "seconds": round(time.time() - t0), "final_message": msg}
            (sdir / "successor.json").write_text(json.dumps(rec, indent=1))
            with (crun / "successors.jsonl").open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            eprint(f"[s{k:02d}] {status}, {n_cmds} commands, final message {len(msg or '')} chars")
        finally:
            rpc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
