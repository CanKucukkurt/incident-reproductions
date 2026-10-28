from __future__ import annotations

import base64
import hashlib
import importlib.util
import shutil
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

StateCheckFn = Callable[[Path, list[dict[str, Any]]], "StateCheckResult"]


class PlantedOp(BaseModel):
    """One file operation that creates the planted problem in a fresh sandbox."""

    op: Literal["delete", "move", "swap", "write", "withhold"]
    path: str | None = None  # delete, write
    src: str | None = None  # move
    dst: str | None = None  # move
    a: str | None = None  # swap
    b: str | None = None  # swap
    content: str | None = None  # write

    @model_validator(mode="after")
    def _check_fields(self) -> "PlantedOp":
        need = {
            "delete": ("path",),
            "withhold": ("path",),  # file is absent from files/; the original lives base64-encoded in planted/ (see planted_original)
            "write": ("path", "content"),
            "move": ("src", "dst"),
            "swap": ("a", "b"),
        }[self.op]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"planted op '{self.op}' requires {missing}")
        return self


class PlantedProblem(BaseModel):
    description: str
    operations: list[PlantedOp] = Field(min_length=1)


class CompactionTrigger(BaseModel):
    kind: Literal["after_turn", "token_threshold"]
    value: int = Field(gt=0)


class StateCheckResult(BaseModel):
    present: bool
    unresolved: bool
    disclosed: bool
    notes: str = ""

    @property
    def ok(self) -> bool:
        return self.present and self.unresolved and not self.disclosed

    def reason(self) -> str:
        if self.ok:
            return "ok"
        bits = []
        if not self.present:
            bits.append("problem_not_present")
        if not self.unresolved:
            bits.append("problem_resolved")
        if self.disclosed:
            bits.append("already_disclosed")
        return "+".join(bits)


class Scenario(BaseModel):
    id: str
    domain: str
    user_request: str
    sandbox_files: str = "files"
    planted_problem: PlantedProblem
    must_disclose: str
    severity: Literal["low", "high"]
    problem_ref: str | None = None  # how the summaries name the problem; fills {problem_ref} in instruction lines
    problem_anchor: str | list[str] | None = None  # regex(es) tried in order; insert_position 'inline' puts the line after the line where the first match ends
    compaction_trigger: CompactionTrigger
    state_check: str = "state_check.py"
    scenario_dir: Path

    @property
    def sandbox_source(self) -> Path:
        return self.scenario_dir / self.sandbox_files

    def load_state_check(self) -> StateCheckFn:
        path = self.scenario_dir / self.state_check
        spec = importlib.util.spec_from_file_location(f"cr_state_check_{self.id}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load state_check from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "check", None)
        if fn is None:
            raise ImportError(f"{path} must define check(sandbox_dir, transcript) -> StateCheckResult")
        return fn

    def run_state_check(self, sandbox_dir: Path, transcript: list[dict[str, Any]]) -> StateCheckResult:
        result = self.load_state_check()(sandbox_dir, transcript)
        if isinstance(result, StateCheckResult):
            return result
        return StateCheckResult.model_validate(result)


PLANTED_DIR = "planted"


def planted_store_name(rel_path: str) -> str:
    """Hashed, extension-less name so a filesystem search for the real filename finds nothing."""
    return hashlib.sha256(rel_path.encode()).hexdigest()[:16] + ".b64"


def planted_original(scenario_dir: Path, rel_path: str) -> bytes:
    """Bytes of a withheld file, decoded from the scenario's planted store."""
    p = Path(scenario_dir) / PLANTED_DIR / planted_store_name(rel_path)
    if not p.is_file():
        raise FileNotFoundError(f"withheld original not in planted store: {p} (for {rel_path})")
    return base64.b64decode(p.read_text())


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise ValueError(f"planted op path escapes sandbox: {rel}")
    return p


def apply_planted_ops(root: Path, ops: list[PlantedOp]) -> None:
    for op in ops:
        if op.op == "delete":
            p = _inside(root, op.path)  # type: ignore[arg-type]
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        elif op.op == "move":
            src = _inside(root, op.src)  # type: ignore[arg-type]
            dst = _inside(root, op.dst)  # type: ignore[arg-type]
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        elif op.op == "swap":
            a = _inside(root, op.a)  # type: ignore[arg-type]
            b = _inside(root, op.b)  # type: ignore[arg-type]
            tmp = a.with_name(a.name + ".cr_swap_tmp")
            a.rename(tmp)
            b.rename(a)
            tmp.rename(b)
        elif op.op == "write":
            p = _inside(root, op.path)  # type: ignore[arg-type]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(op.content or "")
        elif op.op == "withhold":
            p = _inside(root, op.path)  # type: ignore[arg-type]
            if p.exists():
                raise ValueError(f"withheld file must not be in files/: {op.path}")


def materialize_sandbox(scenario: Scenario, dest: Path) -> Path:
    """Copy the scenario's files into ``dest`` and apply the planted problem."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(scenario.sandbox_source, dest, symlinks=False)
    apply_planted_ops(dest, scenario.planted_problem.operations)
    return dest
