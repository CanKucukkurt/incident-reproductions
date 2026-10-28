"""Local environment management. No model is invoked by this CLI."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .config import compose_file


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--project", help="Separate run namespace; defaults to citation")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("up", help="Build and start containers")
    commands.add_parser("status")
    commands.add_parser("shell", help="Open a shell in the agent container")
    execute = commands.add_parser("exec", help="Run an argv command in the agent container")
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    browse = commands.add_parser("open", help="Open a URL with the separate text browser")
    browse.add_argument("url")
    search = commands.add_parser("search", help="Search the live web through Bing RSS")
    search.add_argument("query")
    export = commands.add_parser("export", help="Export browser records")
    export.add_argument("directory", type=Path)
    stop = commands.add_parser("down", help="Stop containers; retain records unless --purge is set")
    stop.add_argument("--purge", action="store_true", help="Delete this project's stored records too")
    args = parser.parse_args()
    project = args.project or "citation"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        parser.error("Project must contain lowercase letters, numbers, underscores, or hyphens")
    # Do not automatically load the user's .env into Compose or any container.
    base = ["docker", "compose", "--env-file", os.devnull, "-p", project,
            "-f", compose_file()]

    def run(tail, **kwargs):
        return subprocess.run(base + tail, check=True, **kwargs)

    try:
        if args.command == "up":
            run(["up", "-d", "--build", "--wait", "--wait-timeout", "90"])
            print("Environment ready: " + project)
        elif args.command == "status":
            run(["ps"])
        elif args.command == "shell":
            run(["exec", "default", "bash"])
        elif args.command == "exec":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not argv:
                parser.error("exec requires a command")
            run(["exec", "-T", "default"] + argv)
        elif args.command in ("open", "search"):
            request = {"operation": args.command}
            request.update({"url": args.url} if args.command == "open" else {"query": args.query})
            run(["exec", "-T", "browser", "python", "-m", "citation_environment.browser"],
                input=json.dumps(request), text=True)
        elif args.command == "export":
            args.directory.mkdir(parents=True, exist_ok=True)
            browser_dir = args.directory / "browser"
            browser_dir.mkdir(exist_ok=True)
            run(["cp", "browser:/logs/.", str(browser_dir.resolve())])
            print("Exported to " + str(args.directory.resolve()))
        elif args.command == "down":
            run(["down"] + (["--volumes"] if args.purge else []))
    except FileNotFoundError:
        parser.exit(1, "Docker was not found. Install and start Docker Desktop or Docker Engine.\n")
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


if __name__ == "__main__":
    main()
