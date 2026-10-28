"""Sandbox snapshots: a copied tree plus a sha256 manifest, verified on restore."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

MANIFEST_NAME = "manifest.json"
FILES_DIR = "files"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(root).as_posix()] = _sha256(p)
    return out


def take_snapshot(src: Path, snapshot_dir: Path) -> dict[str, str]:
    snapshot_dir = Path(snapshot_dir)
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True)
    shutil.copytree(src, snapshot_dir / FILES_DIR, symlinks=False)
    manifest = tree_manifest(snapshot_dir / FILES_DIR)
    (snapshot_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_manifest(snapshot_dir: Path) -> dict[str, str]:
    return json.loads((Path(snapshot_dir) / MANIFEST_NAME).read_text())


def verify_tree(root: Path, manifest: dict[str, str]) -> list[str]:
    """Return a list of mismatches (missing, extra, or changed files); empty means byte-identical."""
    actual = tree_manifest(root)
    problems = []
    for rel, digest in manifest.items():
        if rel not in actual:
            problems.append(f"missing: {rel}")
        elif actual[rel] != digest:
            problems.append(f"changed: {rel}")
    for rel in actual:
        if rel not in manifest:
            problems.append(f"extra: {rel}")
    return problems


def restore_snapshot(snapshot_dir: Path, dest: Path) -> dict[str, str]:
    """Restore a snapshot into a fresh ``dest`` and verify it against the manifest."""
    snapshot_dir = Path(snapshot_dir)
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot_dir / FILES_DIR, dest, symlinks=False)
    manifest = load_manifest(snapshot_dir)
    problems = verify_tree(dest, manifest)
    if problems:
        raise RuntimeError(f"snapshot restore mismatch in {dest}: {problems[:5]}")
    return manifest
