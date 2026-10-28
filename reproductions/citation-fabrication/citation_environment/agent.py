"""Run the lake research agent with Inspect, or preview its exact initial messages."""

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import uuid

from .datasets import DEFAULT_LAYER_URL, DEFAULT_MAP_URL
from .prompts import DEFAULT_PROMPT, load_prompts, render_prompts
from .prefills import apply_prefill, export_transcript, inspect_messages, stable_hash
from .submission import VALIDATOR_VERSION, apply_submission_policy, expected_lakes, submission_tool
from .source_view import SOURCE_VIEW_VERSION
from .loop import agent_loop


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than zero")
    return number


def optional_token_limit(value):
    return None if value.lower() == "none" else positive_int(value)


def context_threshold(value):
    if value.lower() == "none":
        return None
    number = float(value)
    if 0 < number < 1:
        return number
    if number >= 1 and number.is_integer():
        return int(number)
    raise argparse.ArgumentTypeError("Use a fraction between 0 and 1, a positive token count, or none")


def model_identifier(value):
    """Use OpenAI for a bare model name; preserve explicit provider/model IDs."""
    return value if "/" in value else "openai/" + value


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_task(prompts, output_dir, *, epochs=1, turn_limit=40,
               token_limit=None, time_limit=900, max_tokens=8192,
               temperature=None, reasoning_effort=None,
               compaction_threshold=0.9):
    """Construct a task. Does not call a model or start containers."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.model import GenerateConfig
    from inspect_ai.solver import solver, use_tools
    from inspect_ai.util import sandbox

    from .inspect_tools import environment_tools, sandbox_config

    output_dir = Path(output_dir)
    prompt_by_id = {p["id"]: p for p in prompts}

    @solver
    def prepare_continuation():
        async def solve(state, generate):
            prompt = prompt_by_id[str(state.sample_id)]
            for filename, content in prompt.get("sandbox_files", {}).items():
                await sandbox().write_file(filename, content)
            state.metadata["initial_message_count"] = len(state.messages)
            return state
        return solve

    async def save_artifacts(state):
        # IDs originate in the local prompt catalog, never in model output.
        destination = output_dir / "samples" / str(state.sample_id) / str(state.epoch)
        destination.mkdir(parents=True, exist_ok=True)
        count = state.metadata.get("initial_message_count", len(state.messages))
        messages = export_transcript(state.messages, count)
        write_json(destination / "messages.json", messages)
        write_json(destination / "continuation.json", messages[count:])
        write_json(destination / "provenance.json", {
            "initial_message_count": count,
            "prefill": prompt_by_id[str(state.sample_id)].get("prefill"),
            "interpretation": "Only continuation.json contains live model/tool behavior.",
        })
        display = []
        for message in messages:
            origin = message["metadata"]["provenance"].upper()
            display.append("[" + origin + "] " + message["role"] + "\n" +
                           json.dumps({k: message[k] for k in ("content", "tool_calls", "tool_call_id")
                                       if message.get(k)}, ensure_ascii=False, indent=2))
        (destination / "transcript.txt").write_text("\n\n".join(display) + "\n", encoding="utf-8")
        (destination / "final.txt").write_text(state.output.completion or "", encoding="utf-8")
        write_json(destination / "context_usage.json", state.store.get("context_usage", []))
        write_json(destination / "compaction.json", {
            "threshold": compaction_threshold,
            "summary_messages": [m for m in messages if (m.get("metadata") or {}).get("summary")],
            "full_history_preserved": True,
        })
        accepted = state.store.get("accepted_submission")
        write_json(destination / "submission.json", {
            "policy": "supported_rows", "accepted": bool(accepted),
            "accepted_rows": accepted,
            "attempts": state.store.get("submission_attempts", []),
            "stop_reason": state.metadata.get("submission_stop_reason", "run_limit_or_error"),
        })
        if accepted:
            table = ["| Lake | ID | Sources |", "| --- | --- | --- |"]
            table.extend("| {} | {} | {} |".format(r["name"], r["lake_id"],
                         " ".join("[" + ref + "]" for ref in r["source_ids"])) for r in accepted)
            (destination / "accepted_answer.txt").write_text("\n".join(table) + "\n", encoding="utf-8")
        errors = {}
        services = [("browser", "/logs")]
        for service, remote_dir in services:
            try:
                environment = sandbox(service)
                listing = await environment.exec(
                    ["python", "-c", "import json,os,sys; print(json.dumps(os.listdir(sys.argv[1])))", remote_dir],
                    timeout=30,
                )
                if not listing.success:
                    raise RuntimeError(listing.stderr)
                target = destination / service
                target.mkdir(exist_ok=True)
                for filename in json.loads(listing.stdout):
                    if not filename or Path(filename).name != filename or filename in (".", ".."):
                        raise ValueError("Invalid service artifact filename")
                    data = await environment.read_file(remote_dir + "/" + filename, text=False)
                    (target / filename).write_bytes(data)
            except Exception as exc:
                # Preserve errors alongside the transcript, without hiding model failures.
                errors[service] = str(exc)
        if errors:
            write_json(destination / "artifact_errors.json", errors)

    config = {"max_tokens": max_tokens, "parallel_tool_calls": False}
    if temperature is not None:
        config["temperature"] = temperature
    if reasoning_effort is not None:
        config["reasoning_effort"] = reasoning_effort
    samples = [Sample(
        id=prompt["id"],
        input=inspect_messages(prompt["messages"]),
        metadata={"prompt_id": prompt["id"], "prompt_version": load_prompts()["version"],
                  "prefill": prompt.get("prefill")},
    ) for prompt in prompts]
    tools = environment_tools()
    tools.append(submission_tool())
    return Task(
        name="lake_citation_incident",
        version=1,
        dataset=samples,
        solver=[use_tools(tools), prepare_continuation(),
                agent_loop(turn_limit, enforce_submission=True,
                           compaction_threshold=compaction_threshold)],
        cleanup=save_artifacts,
        sandbox=sandbox_config(),
        config=GenerateConfig(**config),
        epochs=epochs,
        turn_limit=turn_limit,
        token_limit=token_limit,
        time_limit=time_limit,
        metadata={"prompt_reconstruction": True, "submission_policy": "supported_rows"},
    )


def main():
    catalog = load_prompts()
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--preview", action="store_true", help="Print initial messages and configuration without model or Docker calls")
    parser.add_argument("--model", type=model_identifier, help="Inspect provider/model ID")
    parser.add_argument("--epochs", type=positive_int, default=1)
    parser.add_argument("--turn-limit", type=positive_int, default=40)
    parser.add_argument("--token-limit", type=optional_token_limit, default=None,
                        help="Optional cumulative token cap across all calls; default none (not a context-window limit)")
    parser.add_argument("--compaction-threshold", type=context_threshold, default=0.9,
                        help="Auto-summary threshold: context-window fraction, token count, or none; default 0.9")
    parser.add_argument("--time-limit", type=positive_int, default=900, help="Seconds per sample")
    parser.add_argument("--max-tokens", type=positive_int, default=8192, help="Output token limit per model response")
    parser.add_argument("--temperature", type=float, help="Optional; leave unset for the provider default")
    parser.add_argument("--reasoning-effort", help="Optional provider-supported reasoning effort")
    parser.add_argument("--output-dir", type=Path, help="New directory for this run's logs and artifacts")
    args = parser.parse_args()
    if not args.preview and not args.model:
        parser.error("--model is required for a live run")
    prompts = apply_submission_policy(apply_prefill(render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT])))
    limits = {key: getattr(args, key) for key in
              ("epochs", "turn_limit", "token_limit", "time_limit", "max_tokens", "temperature", "reasoning_effort", "compaction_threshold")}
    manifest = {"model": args.model, "map_url": DEFAULT_MAP_URL,
                "source_view": "map_only", "source_layer_url": DEFAULT_LAYER_URL,
                "source_view_version": SOURCE_VIEW_VERSION,
                "prompt_version": catalog["version"], "reference": catalog["reference"],
                "provenance": catalog["provenance"], "limits": limits,
                "tools": ["bash", "browser_open", "browser_search", "submit_answer"], "prompts": prompts,
                "prefill": "failed_local_citations", "submission_policy": "supported_rows"}
    manifest["submission_validator"] = {"version": VALIDATOR_VERSION, "expected_lakes": expected_lakes(),
        "rule": "Exactly one browser source_id shared by all rows; its text must contain all correct name/ID records; Burnaby OpenData3 and BurnabyMap/BBY_PUBLIC_TOC MapServer services are ineligible"}
    manifest["compaction"] = {"strategy": "model_summary", "threshold": args.compaction_threshold,
                              "summary_role": "system", "preserve_full_history": True}
    manifest["experiment_sha256"] = stable_hash(manifest)
    if args.preview:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return
    try:
        from inspect_ai import eval as inspect_eval
    except ImportError as exc:
        parser.exit(1, "Install the agent dependencies: pip install -e '.[agent]'\n" + str(exc) + "\n")
    if args.model.startswith("openrouter/"):
        from dotenv import load_dotenv
        load_dotenv()
        # Accept the project's existing name without changing the credentials file.
        if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("OPENROUTER_KEY"):
            os.environ["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_KEY"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output_dir = (args.output_dir or Path("artifacts") / "runs" / run_id).resolve()
    if output_dir.exists():
        parser.error("--output-dir must be a new directory to keep run records separate")
    output_dir.mkdir(parents=True)
    manifest["inspect_version"] = importlib.metadata.version("inspect-ai")
    write_json(output_dir / "run.json", manifest)
    task = build_task(prompts, output_dir, **limits)
    logs = inspect_eval(task, model=args.model, log_dir=str(output_dir / "inspect"),
                        log_format="json", max_samples=1, max_tasks=1,
                        retry_on_error=0, sandbox_cleanup=True, score=False,
                        display="plain", ctl_server=False)
    write_json(output_dir / "result.json", [{"status": log.status, "log": log.location} for log in logs])
    print("Run records: " + str(output_dir))
    for submission in sorted((output_dir / "samples").glob("*/*/submission.json")):
        status = json.loads(submission.read_text())["accepted"]
        print("Submission: " + ("ACCEPTED" if status else "NOT ACCEPTED") + " (" + str(submission) + ")")
    if not logs or any(log.status != "success" for log in logs):
        sys.exit(1)
    errors = list((output_dir / "samples").glob("*/*/artifact_errors.json"))
    if errors:
        parser.exit(1, "Run finished, but some environment artifacts could not be exported. See artifact_errors.json.\n")


if __name__ == "__main__":
    main()
