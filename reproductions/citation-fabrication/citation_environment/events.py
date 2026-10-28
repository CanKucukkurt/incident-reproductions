"""Append-only JSONL records within researcher-owned service volumes."""

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path


def record(path, event, **fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(entry, ensure_ascii=True) + "\n")
        stream.flush()
