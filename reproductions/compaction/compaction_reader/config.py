"""Config models. Secrets never live here: the endpoint key comes from
``CR_API_KEY`` and the base URL from ``CR_BASE_URL`` (or OPENAI_* fallbacks)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent


class LLMConfig(BaseModel):
    api: Literal["chat", "responses"] = "chat"  # responses: OpenAI Responses API (needed for gpt-5.6-sol with tools + reasoning)
    concurrency: int = 4
    max_retries: int = 5
    timeout_s: float = 120.0
    extra_body: dict[str, Any] = Field(default_factory=dict)


class SandboxConfig(BaseModel):
    runner: Literal["docker", "fake"] = "docker"
    image: str = "compaction-reader-sandbox:latest"
    cpus: float = 1.0
    memory: str = "512m"
    pids_limit: int = 256
    timeout_s: float = 60.0
    root: Path | None = None  # temp dir root for sandboxes; default runs/<run_name>/tmp (must be under $HOME for Colima)
    keep_sandboxes: bool = False


class ModelsConfig(BaseModel):
    prefix: str
    under_test: list[str] = Field(min_length=1)
    audit: str


class CutRule(BaseModel):
    """Where to cut a full trajectory into a prefix. ``deepest``: the last turn that still passes the
    state check (i.e. just before the final answer, unless the agent fixed or disclosed the problem
    earlier). ``fraction``: round(value * last_candidate_turn). ``turn``: a fixed turn number."""

    rule: Literal["deepest", "fraction", "turn"] = "deepest"
    value: float | None = None
    min_turn: int = 1
    label: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "CutRule":
        if self.rule in ("fraction", "turn") and self.value is None:
            raise ValueError(f"cut rule '{self.rule}' needs a value")
        if self.rule == "fraction" and not (0 < float(self.value) <= 1):  # type: ignore[arg-type]
            raise ValueError("fraction must be in (0, 1]")
        return self

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        if self.rule == "deepest":
            return "deep"
        if self.rule == "fraction":
            return f"f{int(round(float(self.value) * 100)):02d}"  # type: ignore[arg-type]
        return f"t{int(self.value):02d}"  # type: ignore[arg-type]


class PrefixConfig(BaseModel):
    k: int = 5
    max_turns: int = 40
    max_tokens: int | None = None
    temperature: float | None = None
    run_to_completion: bool = False  # ignore the scenario trigger, run to the final answer, snapshot every turn, cut post hoc
    cuts: list[CutRule] = Field(default_factory=lambda: [CutRule(rule="deepest")])


class SummariesConfig(BaseModel):
    sample_start: int = 0  # first sample index; lets a later run add samples s<start>.. without overwriting earlier ones
    m: int = 3
    compaction_variants: list[str] = Field(default_factory=lambda: ["self_handoff"])
    max_tokens: int | None = 2000
    temperature: float | None = None


class AuditConfig(BaseModel):
    max_retries: int = 3
    max_tokens: int | None = 800


class PrepareConfig(BaseModel):
    run_name: str
    output_root: Path = Path("runs")
    prompts_dir: Path = REPO_ROOT / "prompts"
    scenarios: dict[str, Any] = Field(default_factory=lambda: {"loader": "yaml_dir", "path": str(REPO_ROOT / "scenarios")})
    models: ModelsConfig
    prefix: PrefixConfig = PrefixConfig()
    summaries: SummariesConfig = SummariesConfig()
    audit: AuditConfig = AuditConfig()
    sandbox: SandboxConfig = SandboxConfig()
    llm: LLMConfig = LLMConfig()

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def tmp_root(self) -> Path:
        return self.sandbox.root or (self.run_dir / "tmp")


class BaseCell(BaseModel):
    instruction: str = "none"
    claimed_author: Literal["self", "other_model", "human_engineer"] = "self"
    summary_role: Literal["user", "assistant", "developer", "system"] = "user"
    compaction_variant: str = "self_handoff"
    mode: Literal["agent", "final_answer_only"] = "agent"
    insert_position: str = "append"
    edit_mode: Literal["insert", "replace"] = "insert"  # replace: the line replaces the summary's own located plan sentence(s)


class FollowupConfig(BaseModel):
    enabled: bool = False
    prompt: str = "real_sources"
    tools: bool = True  # True: successor keeps its tools during the follow-up; False: single call, no tools
    only_for: dict[str, list[str]] = Field(default_factory=dict)  # e.g. {"instruction": ["conceal", "none"]}


class SuccessorConfig(BaseModel):
    max_turns: int = 30
    max_tokens: int | None = None  # caps reasoning + output per turn; runaway reasoning otherwise burns the provider's whole budget
    max_degenerate_retries: int = 2  # re-sample a turn that comes back empty with finish length/error
    temperature: float | None = None
    other_model_name: str = "a different model"


class RunConfig(BaseModel):
    run_name: str
    prepare_run: str
    output_root: Path = Path("runs")
    prompts_dir: Path = REPO_ROOT / "prompts"
    scenarios: dict[str, Any] = Field(default_factory=lambda: {"loader": "yaml_dir", "path": str(REPO_ROOT / "scenarios")})
    models_under_test: list[str] | None = None  # default: every model that wrote eligible summaries
    base: BaseCell = BaseCell()
    grid: dict[str, list[str]] = Field(default_factory=lambda: {"instruction": ["none", "honest", "conditional", "conceal"]})
    sweeps: dict[str, list[str]] = Field(default_factory=dict)
    sweep_instructions: list[str] | None = None  # instruction values crossed with each sweep; default grid["instruction"]
    samples_per_cell: int = 1
    max_summaries_per_prefix: int | None = None
    only_summaries: list[str] | None = None  # restrict Stage B to these summary ids
    followup: FollowupConfig = FollowupConfig()
    successor: SuccessorConfig = SuccessorConfig()
    sandbox: SandboxConfig = SandboxConfig()
    llm: LLMConfig = LLMConfig()
    max_parallel_rollouts: int = 4

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def prepare_dir(self) -> Path:
        return self.output_root / self.prepare_run

    @property
    def tmp_root(self) -> Path:
        return self.sandbox.root or (self.run_dir / "tmp")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config must be a mapping: {path}")
    return data


def load_prepare_config(path: str | Path) -> PrepareConfig:
    cfg = PrepareConfig.model_validate(_load_yaml(Path(path)))
    check_compaction_variants(cfg.prompts_dir, cfg.summaries.compaction_variants)
    return cfg


def load_run_config(path: str | Path) -> RunConfig:
    return RunConfig.model_validate(_load_yaml(Path(path)))


def check_compaction_variants(prompts_dir: Path, variants: list[str]) -> None:
    """Every variant is either prompts/compaction/<v>.md or a harness profile prompts/harness/<v>/profile.yaml."""
    for v in variants:
        generic = Path(prompts_dir) / "compaction" / f"{v}.md"
        harness = Path(prompts_dir) / "harness" / v / "profile.yaml"
        if not generic.is_file() and not harness.is_file():
            raise ConfigError(f"compaction variant {v!r}: neither {generic} nor {harness} exists")
