"""Exercise actual Docker environments; external requests are GETs only.

Run from the repository root: python3 scripts/smoke_environment.py
Creates isolated projects and deletes only their test containers and volumes.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent


def main():
    run_id = uuid.uuid4().hex[:10]
    project = "citation-smoke-" + run_id
    base = [sys.executable, "-m", "citation_environment", "--project", project]

    def command(*args):
        result = subprocess.run(base + list(args), cwd=ROOT, text=True,
                                capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout.strip()

    try:
        command("up")
        assert command("exec", "id", "-u") == "1000"
        assert command("exec", "python", "-c",
                       "from pathlib import Path; assert not list(Path('/workspace').iterdir()); "
                       "assert not list(Path('/mnt/data').iterdir()); print('empty')") == "empty"
        assert command("exec", "python", "-c",
                       "import requests; print(requests.get('https://example.com', timeout=15).status_code)") == "200"
        for url in ("file:///mnt/data/test.txt", "http://localhost:8765/test.txt", "http://127.0.0.1/"):
            result = json.loads(command("open", url))
            assert "error" in result and "source_id" not in result, result
        page = json.loads(command("open", "https://example.com"))
        assert "Example Domain" in page["text"] and page["source_id"], page
        search = json.loads(command("search", "example domain"))
        assert search["links"] and "example" in search["text"].lower(), search

        with tempfile.TemporaryDirectory() as directory:
            command("export", directory)
            assert list((Path(directory) / "browser").glob("src_*.json"))
        compose = ["docker", "compose", "--env-file", os.devnull, "-p", project,
                   "-f", str(ROOT / "docker/compose.yaml"), "config", "--services"]
        assert set(subprocess.check_output(compose, text=True).splitlines()) == {"default", "browser"}
        print("Environment: PASS", flush=True)
    finally:
        command("down", "--purge")


if __name__ == "__main__":
    main()
