import subprocess
from pathlib import Path

import pytest

from compaction_reader.cli import make_runner
from compaction_reader.config import SandboxConfig
from compaction_reader.errors import ConfigError, SandboxUnavailable
from compaction_reader.sandbox.docker_runner import DockerRunner


def test_unavailable_daemon_raises(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Cannot connect to the Docker daemon")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SandboxUnavailable, match="never runs on the host"):
        DockerRunner().check_available()


def test_missing_binary_raises(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SandboxUnavailable, match="not found on PATH"):
        DockerRunner(docker_bin="definitely-not-docker").check_available()


def test_missing_image_raises(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such image")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SandboxUnavailable, match="docker build"):
        DockerRunner().check_available()


def test_command_isolation_flags(tmp_path: Path):
    cmd = DockerRunner(image="img", cpus=2, memory="1g", timeout_s=5).command(tmp_path, "cr-test")
    joined = " ".join(cmd)
    assert "--network none" in joined
    assert "--cpus 2" in joined and "--memory 1g" in joined
    assert "--read-only" in joined and "--cap-drop ALL" in joined
    assert "--pids-limit" in joined and "no-new-privileges" in joined
    assert f"-v {tmp_path.resolve()}:/work" in joined
    assert cmd.count("-v") == 1, "only the rollout dir is mounted"
    assert cmd[-3:] == ["img", "python", "-"]


def test_fake_runner_refused_without_mock():
    with pytest.raises(ConfigError, match="only allowed together with --mock"):
        make_runner(SandboxConfig(runner="fake"), mock=None)


def test_docker_runner_is_default_and_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(DockerRunner, "check_available", lambda self: calls.append(1))
    r = make_runner(SandboxConfig(), mock=None)
    assert isinstance(r, DockerRunner) and calls == [1]
