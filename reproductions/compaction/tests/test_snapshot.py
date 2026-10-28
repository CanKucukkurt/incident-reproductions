from pathlib import Path

import pytest

from compaction_reader.sandbox.snapshot import restore_snapshot, take_snapshot, tree_manifest, verify_tree


def test_snapshot_restores_byte_identical(tmp_path: Path):
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "f.bin").write_bytes(bytes(range(256)) * 10)
    (src / "top.txt").write_text("hello\n")
    manifest = take_snapshot(src, tmp_path / "snap")
    assert set(manifest) == {"a/b/f.bin", "top.txt"}
    dest = tmp_path / "restored"
    restore_snapshot(tmp_path / "snap", dest)
    assert (dest / "a" / "b" / "f.bin").read_bytes() == (src / "a" / "b" / "f.bin").read_bytes()
    assert tree_manifest(dest) == manifest
    assert verify_tree(dest, manifest) == []


def test_restore_detects_tampered_snapshot(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("1")
    take_snapshot(src, tmp_path / "snap")
    (tmp_path / "snap" / "files" / "x.txt").write_text("2")
    with pytest.raises(RuntimeError, match="mismatch"):
        restore_snapshot(tmp_path / "snap", tmp_path / "dest")


def test_verify_reports_extra_and_missing(tmp_path: Path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_text("a")
    m = tree_manifest(d)
    (d / "b").write_text("b")
    (d / "a").unlink()
    assert sorted(verify_tree(d, m)) == ["extra: b", "missing: a"]
