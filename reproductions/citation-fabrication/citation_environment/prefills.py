"""Supplied API results and failed local citations for the lake experiment."""

import copy
import hashlib
import json
from pathlib import Path

PREFILL_FILE = Path(__file__).with_name("prompts") / "failed_local_citations.json"


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def apply_prefill(prompts):
    fixture = json.loads(PREFILL_FILE.read_text(encoding="utf-8"))
    # Preserve the provenance and hashes of the condition used in both batches.
    source_fixture_version = 3
    name = "failed_local_citations"
    cut = "Failed local citations supplied; no public hosting or publication idea supplied"
    description = " Continue after failed local citations, without a supplied publication idea."
    result = copy.deepcopy(prompts)
    for prompt in result:
        prompt["base_prompt_id"] = prompt["id"]
        prompt["id"] += "_" + name
        prompt["description"] += description
        prompt["messages"].extend(copy.deepcopy(fixture["history"]))
        prompt["sandbox_files"] = fixture["sandbox_files"]
        prompt["prefill"] = {
            "name": name, "version": fixture["version"],
            "source_fixture_version": source_fixture_version,
            "synthetic_history": True,
            "transport": "completed_assistant_tool_history",
            "history_start_index": 2,
            "initial_message_count": len(prompt["messages"]),
            "fixture_sha256": stable_hash(fixture),
            "context_sha256": stable_hash(prompt["messages"]),
            "data_provenance": fixture["data_provenance"],
            "cut": cut,
        }
    return result


def inspect_messages(messages):
    """Preserve all roles, native tool calls, and their matching results."""
    from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem,
                                  ChatMessageTool, ChatMessageUser)
    classes = {"system": ChatMessageSystem, "user": ChatMessageUser,
               "assistant": ChatMessageAssistant, "tool": ChatMessageTool}
    return [classes[m["role"]].model_validate({**m, "source": "input"}) for m in messages]


def export_transcript(messages, initial_count):
    """Label provenance outside message text so labels never affect model input."""
    exported = []
    for index, message in enumerate(messages):
        item = message.model_dump(mode="json")
        supplied = index < initial_count
        prefill = supplied and index >= 2
        item["metadata"] = {**(item.get("metadata") or {}),
                            "prefill": prefill,
                            "provenance": "prefill" if prefill else "initial" if supplied else
                            "compaction" if (item.get("metadata") or {}).get("summary") else "live"}
        exported.append(item)
    return exported
