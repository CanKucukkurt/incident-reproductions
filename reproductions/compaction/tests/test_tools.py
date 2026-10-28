import json
import os
from pathlib import Path

import pytest

from compaction_reader.errors import PathEscapeError
from compaction_reader.sandbox.tools import FakeRunner, ToolRegistry, confine, load_tool_specs
from tests.conftest import PROMPTS


@pytest.fixture
def reg(tmp_path: Path):
    root = tmp_path / "sb"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_text("content")
    return ToolRegistry(root, runner=FakeRunner(), specs=load_tool_specs(PROMPTS))


def test_confine_rejects_escapes(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    for bad in ["../x", "/etc/passwd", "sub/../../x", "~/x"]:
        with pytest.raises(PathEscapeError):
            confine(root, bad)
    assert confine(root, "a/b") == (root / "a" / "b").resolve()
    assert confine(root, ".") == root.resolve()


def test_confine_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    os.symlink(outside, root / "link")
    with pytest.raises(PathEscapeError):
        confine(root, "link/secret")


async def test_host_tools(reg: ToolRegistry):
    listing = json.loads(await reg.execute("list_files", {}))
    assert listing == [{"path": "sub/f.txt", "bytes": 7}]
    assert await reg.execute("read_file", {"path": "sub/f.txt"}) == "content"
    assert "wrote" in await reg.execute("write_file", {"path": "new/dir/g.txt", "content": "x"})
    assert (reg.root / "new" / "dir" / "g.txt").read_text() == "x"
    assert (await reg.execute("read_file", {"path": "../../etc/passwd"})).startswith("error:")
    assert (await reg.execute("read_file", {"path": "nope.txt"})).startswith("error:")
    assert (await reg.execute("bogus", {})).startswith("error: unknown tool")


async def test_run_python_goes_to_runner_only(reg: ToolRegistry):
    out = await reg.execute("run_python", {"code": "print(1)"})
    assert "fake runner" in out
    assert reg.runner.calls[0]["code"] == "print(1)"
    reg.runner = None
    assert "not available" in await reg.execute("run_python", {"code": "print(1)"})


def test_tool_specs_from_prompts():
    names = [t["function"]["name"] for t in load_tool_specs(PROMPTS)]
    assert names == ["list_files", "read_file", "write_file", "run_python"]
