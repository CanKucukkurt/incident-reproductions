from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


def rollout_id(**fields: Any) -> str:
    """Deterministic id from the identifying fields of a rollout."""
    payload = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class JsonlLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def completed_ids(self, include_errors: bool = False) -> set[str]:
        return {r["rollout_id"] for r in self.iter_records() if include_errors or not r.get("error")}

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
