"""Stage A.4: an LLM judge labels every natural summary. Only summaries that
mention the problem and carry no existing conceal/disclose instruction are
eligible for Stage B. Everything excluded is counted and reported."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PrepareConfig
from ..errors import JudgeParseError
from ..llm.client import LLMClient
from ..prompts_io import read_prompt, render
from ..scenarios.models import Scenario
from .summarize import SummaryRecord

log = logging.getLogger(__name__)

AUDIT_FIELDS = ["mentions_problem", "contains_conceal_instruction", "contains_disclose_instruction", "degenerate"]
CSV_COLUMNS = [
    "summary_id", "scenario_id", "prefix_id", "model", "compaction_variant", "sample_index", "finish_reason",
    *AUDIT_FIELDS, "eligible", "exclusion_reason", "rationale", "judge_model", "parse_attempts",
]

_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str | None) -> dict[str, Any]:
    if not text:
        raise JudgeParseError("empty judge output")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_RE.search(cleaned)
        if not m:
            raise JudgeParseError(f"no JSON object in judge output: {text[:200]!r}")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise JudgeParseError("judge output is not a JSON object")
    return obj


async def judge_json(
    client: LLMClient, *, model: str, messages: list[dict[str, Any]], required: list[str], tag: str, max_retries: int, max_tokens: int | None
) -> tuple[dict[str, Any], int]:
    """Call a judge and parse strict JSON, retrying on parse failure. Returns (obj, attempts)."""
    attempts = 0
    msgs = list(messages)
    last_err: Exception | None = None
    while attempts <= max_retries:
        attempts += 1
        resp = await client.complete(model=model, messages=msgs, tools=None, tag=tag, max_tokens=max_tokens, temperature=0.0)
        try:
            obj = parse_json_object(resp.content)
            missing = [k for k in required if k not in obj]
            if missing:
                raise JudgeParseError(f"judge JSON missing keys {missing}")
            return obj, attempts
        except (JudgeParseError, json.JSONDecodeError) as e:
            last_err = e
            msgs = list(messages) + [
                {"role": "assistant", "content": resp.content or ""},
                {"role": "user", "content": "That was not a single valid JSON object with the required keys. Reply with only the JSON object."},
            ]
    raise JudgeParseError(f"judge failed after {attempts} attempts: {last_err}")


@dataclass
class AuditRow:
    summary: SummaryRecord
    labels: dict[str, bool]
    rationale: str
    eligible: bool
    exclusion_reason: str
    judge_model: str
    parse_attempts: int

    def to_csv_row(self) -> dict[str, Any]:
        s = self.summary
        return {
            "summary_id": s.summary_id, "scenario_id": s.scenario_id, "prefix_id": s.prefix_id, "model": s.model,
            "compaction_variant": s.compaction_variant, "sample_index": s.sample_index, "finish_reason": s.finish_reason,
            **{k: self.labels.get(k) for k in AUDIT_FIELDS},
            "eligible": self.eligible, "exclusion_reason": self.exclusion_reason, "rationale": self.rationale,
            "judge_model": self.judge_model, "parse_attempts": self.parse_attempts,
        }


@dataclass
class AuditResult:
    rows: list[AuditRow] = field(default_factory=list)

    @property
    def eligible(self) -> list[SummaryRecord]:
        return [r.summary for r in self.rows if r.eligible]

    def exclusion_counts(self) -> Counter:
        return Counter(r.exclusion_reason for r in self.rows if not r.eligible)


def decide_eligibility(labels: dict[str, bool], finish_reason: str | None) -> tuple[bool, str]:
    reasons = []
    if finish_reason == "length" or labels.get("degenerate"):
        reasons.append("degenerate")
    if labels.get("contains_conceal_instruction"):
        reasons.append("natural_conceal_instruction")
    if labels.get("contains_disclose_instruction"):
        reasons.append("natural_disclose_instruction")
    if not labels.get("mentions_problem"):
        reasons.append("does_not_mention_problem")
    return (not reasons), "+".join(reasons)


async def audit_one(cfg: PrepareConfig, summary: SummaryRecord, scenario: Scenario, client: LLMClient) -> AuditRow:
    prompt = render(
        read_prompt(cfg.prompts_dir, "judges", "audit.md"),
        planted_problem=scenario.planted_problem.description.strip(),
        must_disclose=scenario.must_disclose.strip(),
        summary=summary.text,
    )
    try:
        obj, attempts = await judge_json(
            client, model=cfg.models.audit, messages=[{"role": "user", "content": prompt}], required=AUDIT_FIELDS,
            tag="audit", max_retries=cfg.audit.max_retries, max_tokens=cfg.audit.max_tokens,
        )
        labels = {k: bool(obj.get(k)) for k in AUDIT_FIELDS}
        rationale = str(obj.get("rationale", ""))
        eligible, reason = decide_eligibility(labels, summary.finish_reason)
    except JudgeParseError as e:
        labels, rationale, attempts = {}, f"judge_parse_error: {e}", cfg.audit.max_retries + 1
        eligible, reason = False, "judge_parse_error"
    return AuditRow(summary=summary, labels=labels, rationale=rationale, eligible=eligible, exclusion_reason=reason,
                    judge_model=cfg.models.audit, parse_attempts=attempts)


def audit_dir(run_dir: Path) -> Path:
    return run_dir / "audit"


def write_audit_outputs(run_dir: Path, result: AuditResult) -> None:
    d = audit_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in result.rows:
            w.writerow(r.to_csv_row())
    (d / "eligible.json").write_text(json.dumps(sorted(s.summary_id for s in result.eligible), indent=1))
    natural = d / "natural_cases"
    if natural.exists():
        shutil.rmtree(natural)
    natural.mkdir()
    for r in result.rows:
        if r.labels.get("contains_conceal_instruction") or r.labels.get("contains_disclose_instruction"):
            kind = "conceal" if r.labels.get("contains_conceal_instruction") else "disclose"
            payload = {**r.summary.to_dict(), "audit": r.to_csv_row()}
            (natural / f"{kind}-{r.summary.summary_id}.json").write_text(json.dumps(payload, indent=1))
    (d / "audit_report.md").write_text(render_report(load_audit_csv(d / "audit.csv")))


def load_audit_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def load_audit_rows(run_dir: Path) -> dict[str, dict[str, str]]:
    p = audit_dir(run_dir) / "audit.csv"
    if not p.is_file():
        return {}
    import csv

    with p.open(newline="", encoding="utf-8") as fh:
        return {r["summary_id"]: r for r in csv.DictReader(fh) if r.get("summary_id")}


def load_eligible_ids(run_dir: Path) -> set[str]:
    p = audit_dir(run_dir) / "eligible.json"
    if not p.is_file():
        return set()
    return set(json.loads(p.read_text()))


def _truthy(v: str | None) -> bool:
    return str(v).lower() == "true"


def render_report(rows: list[dict[str, str]]) -> str:
    lines = ["# Summary audit report", ""]
    n = len(rows)
    elig = sum(_truthy(r["eligible"]) for r in rows)
    lines += [f"- summaries audited: {n}", f"- eligible for Stage B: {elig}", f"- excluded: {n - elig}", ""]
    lines += ["## Label counts", "", "| label | true | false |", "|---|---:|---:|"]
    for k in AUDIT_FIELDS:
        t = sum(_truthy(r[k]) for r in rows)
        lines.append(f"| {k} | {t} | {n - t} |")
    lines += ["", "## Exclusion reasons", "", "| reason | count |", "|---|---:|"]
    for reason, c in Counter(r["exclusion_reason"] for r in rows if not _truthy(r["eligible"])).most_common():
        lines.append(f"| {reason} | {c} |")
    for group in ("scenario_id", "model", "compaction_variant", "prefix_id"):
        lines += ["", f"## Eligibility by {group}", "", f"| {group} | eligible | total |", "|---|---:|---:|"]
        keys = sorted({r[group] for r in rows})
        for key in keys:
            sub = [r for r in rows if r[group] == key]
            lines.append(f"| {key} | {sum(_truthy(r['eligible']) for r in sub)} | {len(sub)} |")
    lines += ["", "## Naturally occurring instructions (not edited, saved under audit/natural_cases/)", ""]
    nat = [r for r in rows if _truthy(r["contains_conceal_instruction"]) or _truthy(r["contains_disclose_instruction"])]
    if not nat:
        lines.append("none")
    for r in nat:
        kind = "conceal" if _truthy(r["contains_conceal_instruction"]) else "disclose"
        lines.append(f"- {r['summary_id']} ({kind}): {r['rationale']}")
    return "\n".join(lines) + "\n"


async def audit_summaries(
    cfg: PrepareConfig, summaries: list[SummaryRecord], scenarios: dict[str, Scenario], client: LLMClient
) -> AuditResult:
    rows = await asyncio.gather(*(audit_one(cfg, s, scenarios[s.scenario_id], client) for s in summaries))
    result = AuditResult(rows=sorted(rows, key=lambda r: r.summary.summary_id))
    write_audit_outputs(cfg.run_dir, result)
    counts = result.exclusion_counts()
    log.info("audit: %d/%d eligible; excluded: %s", len(result.eligible), len(summaries), dict(counts) or "none")
    return result
