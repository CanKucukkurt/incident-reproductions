"""The only implementation of ``run_python``. Model code runs in a throwaway
Docker container with no network, CPU/memory/pid limits, a read-only root
filesystem, and a single bind mount of the rollout directory. If Docker is
unavailable we raise :class:`SandboxUnavailable`; nothing ever runs on the host.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..errors import SandboxUnavailable
from .tools import RunResult

DEFAULT_IMAGE = "compaction-reader-sandbox:latest"


@dataclass
class DockerRunner:
    image: str = DEFAULT_IMAGE
    cpus: float = 1.0
    memory: str = "512m"
    pids_limit: int = 256
    timeout_s: float = 60.0
    tmpfs_size: str = "64m"
    docker_bin: str = "docker"
    run_as_host_user: bool = True
    output_cap_bytes: int = 200_000

    def check_available(self) -> None:
        try:
            proc = subprocess.run([self.docker_bin, "info"], capture_output=True, text=True, timeout=20)
        except FileNotFoundError as e:
            raise SandboxUnavailable(f"'{self.docker_bin}' not found on PATH; model code will not run on the host.") from e
        except subprocess.TimeoutExpired as e:
            raise SandboxUnavailable("docker info timed out; is the daemon running?") from e
        if proc.returncode != 0:
            raise SandboxUnavailable(
                "Docker daemon unavailable (docker info failed):\n" + (proc.stderr or proc.stdout).strip()
                + "\nStart Docker (or `colima start`) and retry. Model code never runs on the host."
            )
        probe = subprocess.run([self.docker_bin, "image", "inspect", self.image], capture_output=True, text=True, timeout=20)
        if probe.returncode != 0:
            raise SandboxUnavailable(
                f"sandbox image '{self.image}' not found. Build it with:\n  docker build -t {self.image} sandbox/"
            )

    def command(self, workdir: Path, container_name: str) -> list[str]:
        cmd = [
            self.docker_bin, "run", "--rm", "-i",
            "--name", container_name,
            "--network", "none",
            "--cpus", str(self.cpus),
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={self.tmpfs_size}",
            "-v", f"{Path(workdir).resolve()}:/work",
            "-w", "/work",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONUNBUFFERED=1",
        ]
        if self.run_as_host_user and hasattr(os, "getuid"):
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += [self.image, "python", "-"]
        return cmd

    async def run(self, code: str, workdir: Path) -> RunResult:
        name = f"cr-{uuid.uuid4().hex[:12]}"
        proc = await asyncio.create_subprocess_exec(
            *self.command(workdir, name),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(code.encode("utf-8")), timeout=self.timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
            await asyncio.create_subprocess_exec(self.docker_bin, "kill", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out, err = b"", b""
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        cap = self.output_cap_bytes
        return RunResult(
            stdout=out[:cap].decode("utf-8", errors="replace"),
            stderr=err[:cap].decode("utf-8", errors="replace"),
            exit_code=-9 if timed_out else (proc.returncode if proc.returncode is not None else -1),
            timed_out=timed_out,
        )
