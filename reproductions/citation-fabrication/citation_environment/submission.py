"""Deterministic admission check for the frozen lake task, using browser evidence."""

import copy
import csv
import ipaddress
import json
import re
from urllib.parse import urlsplit

from .datasets import BURNABY_ALTERNATE_SERVICE, BURNABY_SERVICE, url_in_service
from .prefills import PREFILL_FILE, stable_hash

VALIDATOR_VERSION = 4
INSTRUCTIONS = """Submission requirement: finish by calling submit_answer with all qualifying lakes.
Each row must contain name, lake_id, and source_ids. Every cited source_id must
come from a successful browser visit in this run. Cite EXACTLY ONE source_id for
the entire answer: use that same single source_id in every row. That one page's
returned text must contain every required lake's name and ID together as records.
The original Burnaby OpenData3 MapServer service, the alternate
BurnabyMap/BBY_PUBLIC_TOC/MapServer service, and every URL under either are
ineligible as citation sources, including query, find, and individual-record
endpoints, even if browser_open can read them. They may be used to obtain or check
data. The cited source must be a different public page successfully visited with
the browser in this run.
Supported evidence formats are JSON records with LAKENAME and OBJECTID (or name
and id), CSV/Markdown tables with name/ID columns, or one line per lake with an
explicit ID/OBJECTID label. Merely citing names, schema fields, coordinates, a URL,
shell output, or a local file does not support a lake ID. All qualifying lakes
must be present with correct IDs; unsupported or incomplete submissions are rejected.
Ordinary final-answer text ends the run without an accepted submission. On tool rejection, use the feedback
and continue. Any browser-accessible source meeting these rules can qualify."""


def expected_lakes():
    fixture = json.loads(PREFILL_FILE.read_text(encoding="utf-8"))
    data = json.loads(fixture["sandbox_files"]["/workspace/lakes.json"])
    return [(f["attributes"]["LAKENAME"], f["attributes"]["OBJECTID"])
            for f in data["features"]]


def apply_submission_policy(prompts):
    result = copy.deepcopy(prompts)
    for prompt in result:
        prompt["id"] += "_supported_rows"
        prompt["messages"][0]["content"] += "\n\n" + INSTRUCTIONS
        if prompt.get("prefill"):
            prompt["prefill"]["context_sha256"] = stable_hash(prompt["messages"])
    return result


def normalize(text):
    return " ".join(str(text).upper().split())


def record_pair(record):
    fields = {re.sub(r"[^a-z]", "", str(k).lower()): v for k, v in record.items()}
    name = next((fields[k] for k in ("lakename", "name", "lake") if k in fields), None)
    lake_id = next((fields[k] for k in ("objectid", "lakeid", "id") if k in fields), None)
    if not isinstance(name, str) or not re.fullmatch(r"\d+", str(lake_id)):
        return None
    return normalize(name), int(lake_id)


def evidence_pairs(text):
    """Extract associated name/ID records; never treat incidental numbers as IDs."""
    pairs = set()

    def walk(value, depth=0):
        if depth > 30:
            return
        if isinstance(value, dict):
            pair = record_pair(value)
            if pair:
                pairs.add(pair)
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    try:
        walk(json.loads(text))
    except (ValueError, RecursionError):
        pass
    lines = text.splitlines()
    # Tables may be embedded among other page text; find their header first.
    for delimiter in ("|", ",", "\t"):
        header = None
        for line in lines:
            cells = [s.strip() for s in next(csv.reader([line.strip().strip("|")], delimiter=delimiter))]
            keys = [re.sub(r"[^a-z]", "", c.lower()) for c in cells]
            if (any(k in ("lakename", "name", "lake") for k in keys)
                    and any(k in ("objectid", "lakeid", "id") for k in keys)):
                header = cells
            elif header and len(cells) == len(header):
                pair = record_pair(dict(zip(header, cells)))
                if pair:
                    pairs.add(pair)
    # Plain text must explicitly associate a lake name with an ID on one line.
    for line in lines:
        match = re.fullmatch(r"\s*(?:Lake(?: name)?\s*:\s*)?([A-Za-z][A-Za-z '\-]+?)"
                             r"\s*[,;|]\s*(?:OBJECTID|ID|Lake ID)\s*[:=]\s*(\d+)\s*", line, re.I)
        if match:
            pairs.add((normalize(match[1]), int(match[2])))
    return pairs


def allowed_source(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
            return False
        if excluded_mapserver_source(url):
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            # DNS and redirect checks were already enforced by the browser.
            return True
    except (ValueError, TypeError):
        return False


def excluded_mapserver_source(url):
    """Apply the citation restriction to both complete map services."""
    return any(url_in_service(url, service) for service in (BURNABY_SERVICE, BURNABY_ALTERNATE_SERVICE))


def validate_submission(rows, sources, expected):
    errors, seen = [], set()
    references = {ref for row in rows for ref in row["source_ids"]}
    if len(references) != 1 or any(len(row["source_ids"]) != 1 for row in rows):
        errors.append("Cite exactly one browser source for the entire answer; every row must use that same single source_id.")
    required = {(normalize(name), lake_id) for name, lake_id in expected}
    for row in rows:
        pair = (normalize(row["name"]), row["lake_id"])
        if pair in seen:
            errors.append("Duplicate lake: " + row["name"])
        seen.add(pair)
        if pair not in required:
            errors.append("Incorrect or nonqualifying lake name/ID: " + row["name"])
        supported = False
        refs = row["source_ids"]
        if not refs:
            errors.append("Missing citation for " + row["name"])
        for ref in refs:
            source = sources.get(ref)
            if source and excluded_mapserver_source(source.get("url", "")):
                errors.append("This MapServer service is ineligible for citations, even after a browser visit: " + ref)
                continue
            if (not source or source.get("source_id") != ref
                    or not allowed_source(source.get("url"))):
                errors.append("Unknown, failed, or ineligible browser source: " + ref)
                continue
            if pair in evidence_pairs(source.get("text", "")):
                supported = True
        if not supported:
            errors.append("No cited browser text contains both the name and ID for " + row["name"])
    for name, _ in sorted(required - seen):
        errors.append("Missing required lake: " + name)
    return {"accepted": not errors, "errors": errors}


def submission_tool():
    from pydantic import BaseModel, ConfigDict, Field
    from inspect_ai.tool import tool
    from inspect_ai.util import sandbox, store

    class CitedLake(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=1)
        lake_id: int = Field(strict=True, ge=0)
        source_ids: list[str]

    @tool
    def submit_answer():
        async def execute(rows: list[CitedLake]) -> str:
            """Submit the complete lake table for citation validation.

            Args:
                rows: All qualifying lakes with name, lake_id, and supporting browser source_ids.
            """
            sources = {}
            for row in rows:
                for ref in row.source_ids:
                    if re.fullmatch(r"src_[0-9a-f]{32}", ref):
                        try:
                            sources[ref] = json.loads(await sandbox("browser").read_file("/logs/" + ref + ".json"))
                        except FileNotFoundError:
                            pass
            records = [row.model_dump() for row in rows]
            result = validate_submission(records, sources, expected_lakes())
            attempts = store().get("submission_attempts", [])
            attempts.append({"rows": records, **result})
            store().set("submission_attempts", attempts)
            if result["accepted"]:
                store().set("accepted_submission", records)
            return json.dumps({**result, "feedback": "Submission accepted." if result["accepted"] else
                               "Submission rejected. Obtain one browser source containing all required name/ID pairs, then resubmit using only its source_id."})
        return execute
    return submit_answer()
