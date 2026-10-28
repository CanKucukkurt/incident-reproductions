"""Locate, verbatim, the sentences in a summary that plan what to tell the user about the
planted problem. Feeds ``edit_mode: replace`` in Stage B: the instruction line replaces the
summary's own plan instead of being added next to it.

Outputs under ``audit/``: ``plan_sentences.json`` ({summary_id: {disclose_plans: [{sentence,
clause}], conceal_plans: [...], unverified, judge_model, rationale}}) and ``eligible_replace.json``
(the summary ids with at least one verified disclose plan and no conceal plan). ``clause`` is the
minimal span inside ``sentence`` that carries the tell-the-user content; the edit removes only that."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..config import PrepareConfig
from ..llm.client import LLMClient
from ..prompts_io import read_prompt, render
from ..scenarios.models import Scenario
from .audit import audit_dir, judge_json
from .summarize import SummaryRecord

log = logging.getLogger(__name__)
REQUIRED = ["disclose_plans", "conceal_plans"]


def find_verbatim(text: str, sentence: str) -> str | None:
    """Return the exact substring of ``text`` the judge meant, or None. Tolerates the judge
    trimming whitespace, dropping a trailing period, or collapsing runs of spaces."""
    s = sentence.strip()
    found: str | None = None
    for cand in (s, s.rstrip("."), s.rstrip(".").rstrip()):
        if cand and cand in text:
            found = cand
            break
    if found is None:  # whitespace-insensitive match, mapped back onto the original span
        pat = r"\s+".join(re.escape(w) for w in s.split())
        m = re.search(pat, text)
        found = m.group(0) if m else None
    if found is not None and not found.endswith(".") and text[text.index(found) + len(found) : text.index(found) + len(found) + 1] == ".":
        found += "."  # keep the sentence's own full stop with it, so the replacement does not leave a stray period
    return found


def _verify(text: str, plans: list[Any]) -> tuple[list[dict[str, str]], list[Any]]:
    """Map the judge's {sentence, clause} pairs onto verbatim spans of ``text``; unmatched ones go to ``bad``."""
    ok, bad = [], []
    for pl in plans or []:
        if isinstance(pl, str):
            pl = {"sentence": pl, "clause": pl}
        if not isinstance(pl, dict) or not str(pl.get("sentence", "")).strip():
            continue
        sent = find_verbatim(text, str(pl["sentence"]))
        if sent is None:
            bad.append(pl)
            continue
        clause_raw = str(pl.get("clause") or pl["sentence"])
        clause = find_verbatim(sent, clause_raw)
        if clause is None or clause.strip() == "":
            clause = sent  # fall back to the whole sentence
        ok.append({"sentence": sent, "clause": clause})
    return ok, bad


async def locate_plans(
    cfg: PrepareConfig, summaries: list[SummaryRecord], scenarios: dict[str, Scenario], client: LLMClient, *, model: str | None = None
) -> dict[str, dict[str, Any]]:
    judge_model = model or cfg.models.audit
    template = read_prompt(cfg.prompts_dir, "judges", "locate_plan.md")
    d = audit_dir(cfg.run_dir)
    d.mkdir(parents=True, exist_ok=True)
    out_path = d / "plan_sentences.json"
    results: dict[str, dict[str, Any]] = json.loads(out_path.read_text()) if out_path.is_file() else {}

    async def one(s: SummaryRecord) -> None:
        sc = scenarios[s.scenario_id]
        prompt = render(template, planted_problem=sc.planted_problem.description.strip(), must_disclose=sc.must_disclose.strip(), summary=s.text)
        obj, attempts = await judge_json(
            client, model=judge_model, messages=[{"role": "user", "content": prompt}], required=REQUIRED, tag="locate",
            max_retries=cfg.audit.max_retries, max_tokens=cfg.audit.max_tokens,
        )
        disc_ok, disc_bad = _verify(s.text, obj.get("disclose_plans"))
        conc_ok, conc_bad = _verify(s.text, obj.get("conceal_plans"))
        if disc_bad or conc_bad:
            log.warning("locate %s: %d sentence(s) not found verbatim", s.summary_id, len(disc_bad) + len(conc_bad))
        results[s.summary_id] = {
            "scenario_id": s.scenario_id, "disclose_plans": disc_ok, "conceal_plans": conc_ok,
            "unverified": disc_bad + conc_bad, "judge_model": judge_model, "attempts": attempts, "rationale": obj.get("rationale", ""),
        }

    await asyncio.gather(*(one(s) for s in summaries))
    out_path.write_text(json.dumps(results, indent=1, sort_keys=True))
    eligible = sorted(sid for sid, r in results.items() if r["disclose_plans"] and not r["conceal_plans"])
    (d / "eligible_replace.json").write_text(json.dumps(eligible, indent=1))
    return results


def load_plan_sentences(run_dir: Path) -> dict[str, dict[str, Any]]:
    p = audit_dir(run_dir) / "plan_sentences.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def load_eligible_replace_ids(run_dir: Path) -> set[str]:
    p = audit_dir(run_dir) / "eligible_replace.json"
    return set(json.loads(p.read_text())) if p.is_file() else set()
