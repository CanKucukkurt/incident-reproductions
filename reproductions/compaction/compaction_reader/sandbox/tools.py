"""Agent tools. ``list_files``/``read_file``/``write_file`` run host-side on the
rollout directory with strict path confinement. ``run_python`` is delegated to
a :class:`PythonRunner`; the only real implementation is the Docker runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from ..errors import PathEscapeError


def confine(root: Path, rel: str | None) -> Path:
    """Resolve ``rel`` inside ``root``; refuse anything that escapes (including via symlinks)."""
    root_r = Path(root).resolve()
    rel = (rel or ".").strip()
    if rel.startswith("/") or rel.startswith("~"):
        raise PathEscapeError(f"absolute paths are not allowed: {rel}")
    candidate = (root_r / rel).resolve()
    if candidate != root_r and root_r not in candidate.parents:
        raise PathEscapeError(f"path escapes sandbox: {rel}")
    # Refuse any symlink component that points outside root.
    cur = root_r / rel
    for part in [cur, *cur.parents]:
        if part == root_r or root_r not in part.parents:
            break
        if part.is_symlink():
            target = part.resolve()
            if target != root_r and root_r not in target.parents:
                raise PathEscapeError(f"symlink escapes sandbox: {rel}")
    return candidate


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    def render(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr)
        if self.timed_out:
            parts.append("[killed: time limit exceeded]")
        parts.append(f"[exit code {self.exit_code}]")
        return "\n".join(parts)


class PythonRunner(Protocol):
    async def run(self, code: str, workdir: Path) -> RunResult: ...


@dataclass
class FakeRunner:
    """Does not execute anything. Only usable with the mock client (tests, quickstart)."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, code: str, workdir: Path) -> RunResult:
        self.calls.append({"code": code, "workdir": str(workdir)})
        return RunResult(stdout=f"[fake runner: code was not executed; workdir={Path(workdir).name}]", stderr="", exit_code=0)


def load_tool_specs(prompts_dir: Path, names: list[str] | None = None) -> list[dict[str, Any]]:
    data = yaml.safe_load((Path(prompts_dir) / "agent" / "tools.yaml").read_text())
    specs = []
    for t in data["tools"]:
        if names and t["name"] not in names:
            continue
        specs.append({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}})
    return specs


def _truncate(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n...[truncated {len(s) - cap} chars]"


class ToolRegistry:
    def __init__(
        self,
        root: Path,
        *,
        runner: PythonRunner | None,
        specs: list[dict[str, Any]],
        max_read_chars: int = 40_000,
        max_output_chars: int = 20_000,
    ) -> None:
        self.root = Path(root).resolve()
        self.runner = runner
        self._specs = specs
        self.max_read_chars = max_read_chars
        self.max_output_chars = max_output_chars

    @property
    def specs(self) -> list[dict[str, Any]]:
        return self._specs

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "list_files":
                return self._list_files(args.get("path") or ".")
            if name == "read_file":
                return self._read_file(args["path"])
            if name == "write_file":
                return self._write_file(args["path"], args.get("content", ""))
            if name == "run_python":
                if self.runner is None:
                    return "error: run_python is not available in this session"
                res = await self.runner.run(args.get("code", ""), self.root)
                return _truncate(res.render(), self.max_output_chars)
            return f"error: unknown tool '{name}'"
        except PathEscapeError as e:
            return f"error: {e}"
        except KeyError as e:
            return f"error: missing argument {e}"
        except FileNotFoundError as e:
            return f"error: file not found: {Path(str(e.filename)).name if e.filename else e}"
        except IsADirectoryError:
            return "error: path is a directory"
        except UnicodeDecodeError:
            return "error: file is not valid UTF-8 text"

    def _list_files(self, rel: str) -> str:
        base = confine(self.root, rel)
        if not base.exists():
            return f"error: no such path: {rel}"
        entries = []
        for p in sorted(base.rglob("*")):
            if p.is_dir():
                continue
            entries.append({"path": p.relative_to(self.root).as_posix(), "bytes": p.stat().st_size})
        return json.dumps(entries, indent=1)

    def _read_file(self, rel: str) -> str:
        p = confine(self.root, rel)
        return _truncate(p.read_text(encoding="utf-8"), self.max_read_chars)

    def _write_file(self, rel: str, content: str) -> str:
        p = confine(self.root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content.encode('utf-8'))} bytes to {rel}"
