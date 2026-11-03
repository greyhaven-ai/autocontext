from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from autocontext.kernel_evolution import docker_watchdog


@pytest.mark.parametrize("label", [docker_watchdog.DOCKER_KERNEL_OWNER_LABEL, "ai.autocontext.weight-worker"])
def test_watchdog_removes_only_containers_with_selected_owner_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str,
) -> None:
    calls: list[list[str]] = []
    listed = False

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal listed
        calls.append(command)
        if command[1:3] == ["ps", "-aq"]:
            expected_filter = f"label={label}=worker-123"
            assert command == ["docker", "ps", "-aq", "--filter", expected_filter]
            stdout = "owned-container\n" if not listed else ""
            listed = True
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        assert command == ["docker", "rm", "-f", "owned-container"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_watchdog.subprocess, "run", run)
    monkeypatch.setattr(docker_watchdog.time, "sleep", lambda _seconds: None)
    result = docker_watchdog.run_deadline_watchdog(
        "docker", "worker-123", time.time() - 1, tmp_path / "ready", owner_label=label,
    )
    assert result == 0
    assert calls[1] == ["docker", "rm", "-f", "owned-container"]
    assert len(calls) == 3


@pytest.mark.parametrize("custom", [False, True])
def test_detached_launch_and_cli_preserve_owner_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, custom: bool,
) -> None:
    label = "ai.autocontext.weight-worker" if custom else docker_watchdog.DOCKER_KERNEL_OWNER_LABEL
    ready = tmp_path / "ready"
    ready.write_text("ready\n")
    process = Mock()
    popen = Mock(return_value=process)
    execute = Mock(return_value=0)
    monkeypatch.setattr(docker_watchdog.subprocess, "Popen", popen)
    monkeypatch.setattr(docker_watchdog, "run_deadline_watchdog", execute)
    result = docker_watchdog.launch_deadline_watchdog(
        "docker", "worker-123", 2000000000.0, ready, owner_label=label,
    )
    assert result is process
    command = popen.call_args.args[0]
    assert len(command) == (9 if custom else 8)
    assert popen.call_args.kwargs["start_new_session"] is True
    assert docker_watchdog._entrypoint(command[1:]) == 0
    assert execute.call_args.args[:4] == ("docker", "worker-123", 2000000000.0, ready)
    assert execute.call_args.kwargs == {"owner_label": label}


@pytest.mark.parametrize("label", ["", "label=anything", "key,other", "--filter", "key\n", "a" * 129])
def test_watchdog_rejects_unsafe_owner_filter_before_starting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str,
) -> None:
    popen = Mock()
    monkeypatch.setattr(docker_watchdog.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="owner label"):
        docker_watchdog.launch_deadline_watchdog("docker", "worker-123", time.time(), tmp_path / "ready", owner_label=label)
    assert docker_watchdog.run_deadline_watchdog(
        "docker", "worker-123", time.time(), tmp_path / "ready", owner_label=label,
    ) == 2
    assert not (tmp_path / "ready").exists()
    popen.assert_not_called()
