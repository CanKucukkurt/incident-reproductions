"""Stage A.3: for each model under test and each kept prefix, sample M
compaction summaries using a compaction prompt variant. The compaction prompt is
appended as a user message to the prefix transcript."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import PrepareConfig
from ..llm.client import LLMClient
from ..harness import build_summarizer_request, is_harness_variant, load_profile, postprocess
from ..sandbox.tools import load_tool_specs
from ..prompts_io import read_prompt, slug
from .prefix import PrefixRecord

log = logging.getLogger(__name__)


@dataclass
class SummaryRecord:
    summary_id: str
    scenario_id: str
    prefix_id: str
    model: str
    compaction_variant: str
    sample_index: int
    text: str
    finish_reason: str | None
    usage: dict[str, int]
    reasoning: str | None
    raw_text: str | None = None  # harness variants: the summariser's reply before post-processing
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "path"}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], path: Path | None = None) -> "SummaryRecord":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k != "path"}, path=path)  # type: ignore[arg-type]


def normalize_summary(text: str | None) -> str:
    """Stored summaries end with exactly one newline so instruction insertion yields a clean diff."""
    return (text or "").rstrip() + "\n"


def summaries_dir(run_dir: Path) -> Path:
    return run_dir / "summaries"


def load_summaries(run_dir: Path) -> list[SummaryRecord]:
    root = summaries_dir(run_dir)
    out = []
    for p in sorted(root.rglob("*.json")) if root.is_dir() else []:
        out.append(SummaryRecord.from_dict(json.loads(p.read_text()), path=p))
    return out


async def summarize_one(
    cfg: PrepareConfig, prefix: PrefixRecord, model: str, variant: str, sample_index: int, client: LLMClient
) -> SummaryRecord:
    transcript = prefix.load_transcript()
    profile = load_profile(cfg.prompts_dir, variant) if is_harness_variant(cfg.prompts_dir, variant) else None
    raw_text: str | None = None
    if profile is None:
        messages = list(transcript["messages"]) + [{"role": "user", "content": read_prompt(cfg.prompts_dir, "compaction", f"{variant}.md")}]
        resp = await client.complete(
            model=model, messages=messages, tools=None, tag="summary",
            max_tokens=cfg.summaries.max_tokens, temperature=cfg.summaries.temperature,
        )
        text = normalize_summary(resp.content)
    else:
        req = build_summarizer_request(profile, transcript["messages"])
        tools = load_tool_specs(cfg.prompts_dir) if req.tools else None
        resp = await client.complete(
            model=model, messages=req.messages, tools=tools, tag="summary",
            max_tokens=cfg.summaries.max_tokens, temperature=cfg.summaries.temperature,
        )
        raw_text = resp.content or ""
        if req.verify and raw_text.strip():
            # Gemini CLI style verification pass: the draft goes back as the assistant turn
            resp2 = await client.complete(
                model=model,
                messages=req.messages + [{"role": "assistant", "content": raw_text}, {"role": "user", "content": req.verify.strip()}],
                tools=None, tag="summary", max_tokens=cfg.summaries.max_tokens, temperature=cfg.summaries.temperature,
            )
            if (resp2.content or "").strip():
                raw_text = resp2.content or ""
                resp = resp2
        text = normalize_summary(postprocess(profile, raw_text))
    summary_id = f"{prefix.prefix_id}-{slug(model)}-{variant}-s{sample_index:02d}"
    rec = SummaryRecord(
        summary_id=summary_id,
        scenario_id=prefix.scenario_id,
        prefix_id=prefix.prefix_id,
        model=model,
        compaction_variant=variant,
        sample_index=sample_index,
        text=text,
        raw_text=raw_text,
        finish_reason=resp.finish_reason,
        usage=resp.usage,
        reasoning=resp.reasoning,
    )
    d = summaries_dir(cfg.run_dir) / prefix.scenario_id / prefix.prefix_id
    d.mkdir(parents=True, exist_ok=True)
    rec.path = d / f"{summary_id}.json"
    rec.path.write_text(json.dumps(rec.to_dict(), indent=1))
    return rec


async def write_summaries(cfg: PrepareConfig, prefixes: list[PrefixRecord], client: LLMClient) -> list[SummaryRecord]:
    jobs = [
        summarize_one(cfg, p, model, variant, m, client)
        for p in prefixes
        for model in cfg.models.under_test
        for variant in cfg.summaries.compaction_variants
        for m in range(cfg.summaries.sample_start, cfg.summaries.sample_start + cfg.summaries.m)
    ]
    recs = await asyncio.gather(*jobs)
    log.info("wrote %d summaries", len(recs))
    return list(recs)
