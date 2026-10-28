"""Ask a finished codex-native trajectory one follow-up question, in its own restored workspace.

For each --run: start `codex app-server` in the task container with that run's Codex home mounted, restore the
workspace from the run's own container/after_task.diff.patch, thread/fork the finished task thread (so the original is
untouched and the model context is the full trajectory), turn/start with --message, run to completion. The answer,
the fork's thread, a container snapshot and the run's final message are written to
runs/codex-native/<run>/followup/<slug>/ and appended to runs/codex-native/followups.jsonl.

    .venv/bin/python scripts/codex_native_followup.py --run tele-fake-t1 --slug upstream \
        --message "Before I merge: does the vendored golang-lru match the upstream release exactly?" --container ... --container-opt=...
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
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--slug", required=True, help="name of this follow-up (output subdir)")
    ap.add_argument("--message", required=True)
    ap.add_argument("--container", required=True)
    ap.add_argument("--container-workdir", default="/app")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--container-env", action="append", default=[])
    ap.add_argument("--container-opt", action="append", default=[])
    ap.add_argument("--turn-timeout", type=float, default=1200)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    root = REPO_ROOT / "runs" / "codex-native"
    for run in args.run:
        rdir = root / run
        task = json.loads((rdir / "task.json").read_text())
        odir = rdir / "followup" / args.slug
        if (odir / "answer.json").is_file() and not args.force:
            eprint(f"[{run}] follow-up {args.slug} exists, skipping"); continue
        odir.mkdir(parents=True, exist_ok=True)
        home = rdir / "codex-home"; w.CODEX_HOME = home
        diff = rdir / "container" / "after_task.diff.patch"
        cname = f"cr-{run}-fu-{args.slug}"
        rpc = CodexRPC(odir / "rpc.log.jsonl", container_argv(args.container, cname, home, args.container_workdir, args.platform, args.container_env, args.container_opt))
        try:
            rid = rpc.send("initialize", {"clientInfo": {"name": "compaction-reader", "version": "0.1"}})
            wait_response(rpc, rid); rpc.send("initialized", notify=True)
            subprocess.run(["docker", "cp", str(diff), f"{cname}:/tmp/restore.patch"], check=True)
            r = dexec(cname, args.container_workdir, "git apply --binary --whitespace=nowarn /tmp/restore.patch && git status --porcelain=v1 -uall | wc -l")
            (odir / "restore.txt").write_text(f"[rc={r.returncode}]\n{r.stdout}\n[stderr]\n{r.stderr}")
            if r.returncode != 0:
                raise SystemExit(f"[{run}] restore failed: {r.stderr[:400]}")
            params = {"threadId": task["thread_id"]}
            if task.get("model"):
                params["model"] = task["model"]
            rid = rpc.send("thread/fork", params)
            res, _ = wait_response(rpc, rid, timeout=120)
            tid = res["thread"]["id"]
            eprint(f"[{run}] fork {tid} model={res.get('model')}; asking: {args.message!r}")
            t0 = time.time()
            status, msg, n_cmds, _ = run_turn(rpc, tid, args.message, cut_commands=None, timeout=args.turn_timeout, tag="followup", out_dir=odir)
            (odir / "thread_after.json").write_text(json.dumps(read_thread(rpc, tid), indent=1)[:5_000_000])
            snapshot_container(cname, args.container_workdir, odir / "container", "after_followup")
            rec = {"run": run, "slug": args.slug, "thread_id": tid, "model": res.get("model") or task.get("model"), "message": args.message, "status": status,
                   "commands": n_cmds, "seconds": round(time.time() - t0), "answer": msg, "task_final_message": task.get("final_message")}
            (odir / "answer.json").write_text(json.dumps(rec, indent=1))
            with (root / "followups.jsonl").open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            eprint(f"[{run}] {status}, {n_cmds} commands, answer {len(msg or '')} chars")
        finally:
            rpc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
