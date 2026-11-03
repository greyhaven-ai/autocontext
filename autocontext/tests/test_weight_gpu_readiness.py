from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.weight_evolution import gpu_readiness as readiness
from autocontext.weight_evolution.runtime import RuntimeLock

GPU = "GPU-01234567-89ab-cdef-0123-456789abcdef"
IMAGE = "example.invalid/qwen:v1@sha256:" + "a" * 64


@pytest.fixture
def runtime() -> RuntimeLock:
    return RuntimeLock(schema_version=1, image=IMAGE, python_version="3.12",
                       packages=({"name": "vllm", "version": "0.32.0"},))


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(readiness.platform, "system", lambda: "Linux")
    monkeypatch.setattr(readiness.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(readiness.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(readiness.shutil, "disk_usage", lambda path: SimpleNamespace(free=100_000_000_000))
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    data: dict[str, Any] = {
        "context": [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}],
        "info": {"ID": "daemon-id", "OSType": "linux", "Architecture": "x86_64", "ServerVersion": "29.1.0",
                 "Runtimes": {"runc": {}, "nvidia": {}}, "MemTotal": 32 * 1024**3, "NCPU": 8},
        "image": [{"Id": "sha256:" + "b" * 64, "Os": "linux", "Architecture": "amd64", "RepoDigests": [IMAGE],
                   "Config": {"Env": ["CUDA_VERSION=13.0.2", "NVIDIA_REQUIRE_CUDA=cuda>=13.0", "SECRET=not-reported"]}}],
        "nvidia": f"0, {GPU}, NVIDIA Test, 580.1.2, 81920, 80000, 9.0, Disabled\n",
        "calls": [],
    }

    def query(argv: list[str], timeout: float) -> readiness._CommandResult:
        data["calls"].append(argv)
        if argv[0].endswith("nvidia-smi"):
            key = "nvidia"
        else:
            key = argv[1]
        value = data[key]
        return value if isinstance(value, readiness._CommandResult) else readiness._CommandResult(
            0, value if isinstance(value, str) else json.dumps(value),
        )

    monkeypatch.setattr(readiness, "_run_command", query)
    return data


def probe(runtime: RuntimeLock, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return readiness.probe_gpu_readiness(
        runtime=runtime, gpu_device=kwargs.pop("gpu_device", "0"), output_parent=tmp_path,
        required_free_bytes=40_000_000_000, **kwargs,
    )


def codes(report: dict[str, Any]) -> set[str]:
    return {item["code"] for item in report["blockers"]}


def test_ready_is_only_read_only_prerequisites(host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path) -> None:
    report = probe(runtime, tmp_path)
    assert report["ready_for_acceptance"]
    assert not report["model_execution_performed"]
    assert not report["container_execution_performed"]
    assert report["compatibility"] == "requires_live_acceptance"
    assert report["gpu_memory_isolation"] == "not_attested"
    assert report["observations"]["selected_gpu"]["uuid"] == GPU
    assert report["observations"]["daemon"]["id"] == "daemon-id"
    assert report["runtime_digest"] == runtime.digest
    assert "SECRET" not in json.dumps(report)
    assert [call[1] for call in host["calls"][:3]] == ["context", "info", "image"]
    assert not any("run" in call or "pull" in call or "create" in call for call in host["calls"])


def test_full_gpu_uuid_selected_and_canonicalized(host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path) -> None:
    upper = "GPU-" + GPU[4:].upper()
    host["nvidia"] = host["nvidia"].replace(GPU, upper)
    report = probe(runtime, tmp_path, gpu_device=upper)
    assert report["ready_for_acceptance"]
    assert report["observations"]["selected_gpu"]["uuid"] == GPU
    assert f"--id={upper}" in host["calls"][-1]


@pytest.mark.parametrize("device", ["all", "0,1", "0;echo unsafe", "MIG-12345", "-1", "GPU-short"])
def test_ambiguous_devices_rejected_before_queries(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, device: str,
) -> None:
    with pytest.raises(ValueError, match="physical GPU"):
        probe(runtime, tmp_path, gpu_device=device)
    assert host["calls"] == []


def test_darwin_client_and_arm_daemon_are_separate_blockers(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(readiness.platform, "machine", lambda: "arm64")
    host["info"]["Architecture"] = "aarch64"
    report = probe(runtime, tmp_path)
    assert codes(report) == {"coordinator_platform", "daemon_platform"}
    assert not report["ready_for_acceptance"]


@pytest.mark.parametrize("endpoint", ["ssh://gpu.example", "tcp://127.0.0.1:2375", "npipe:////./pipe/docker_engine"])
def test_remote_daemon_not_confused_with_local_gpu(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    report = probe(runtime, tmp_path)
    assert "remote_docker_unsupported" in codes(report)
    assert not any(call[0].endswith("docker") for call in host["calls"])
    assert endpoint not in json.dumps(report)


def test_docker_host_override_matches_runner_environment(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "ignored-by-runner")
    assert probe(runtime, tmp_path)["ready_for_acceptance"]
    assert not any("context" in call for call in host["calls"])


def test_insufficient_resources_fail_closed(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness.shutil, "disk_usage", lambda path: SimpleNamespace(free=100))
    host["info"].update(MemTotal=1024, NCPU=1)
    report = probe(runtime, tmp_path)
    assert codes(report) == {"insufficient_disk", "insufficient_host_memory", "insufficient_host_cpu"}


def test_nested_output_uses_existing_ancestor_without_creating_directories(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected: list[Path] = []

    def disk_usage(path: Path) -> SimpleNamespace:
        inspected.append(path)
        return SimpleNamespace(free=100_000_000_000)

    monkeypatch.setattr(readiness.shutil, "disk_usage", disk_usage)
    requested = tmp_path / "new-parent" / "new-study"
    report = probe(runtime, requested)
    assert report["ready_for_acceptance"]
    assert inspected == [tmp_path.resolve()]
    assert report["observations"]["disk"]["path"] == str(tmp_path.resolve())
    assert report["observations"]["disk"]["requested_path"] == str(requested.resolve())
    assert not (tmp_path / "new-parent").exists()


def test_existing_file_cannot_be_output_ancestor(host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path) -> None:
    existing = tmp_path / "regular-file"
    existing.write_text("fixture")
    with pytest.raises(ValueError, match="must be a directory"):
        probe(runtime, existing / "new-study")


@pytest.mark.parametrize("change", ["digest", "arch", "missing"])
def test_image_is_local_and_exact(host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, change: str) -> None:
    if change == "missing":
        host["image"] = readiness._CommandResult(1, error="not installed")
        expected = "pinned_image_unavailable"
    else:
        host["image"][0].update({"RepoDigests": []} if change == "digest" else {"Architecture": "arm64"})
        expected = "pinned_image_mismatch"
    assert expected in codes(probe(runtime, tmp_path))


@pytest.mark.parametrize("change", ["wrong_index", "multiple", "memory", "mig_enabled", "mig_unknown", "capability"])
def test_bad_or_ambiguous_gpu_observations_fail(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, change: str,
) -> None:
    changes = {
        "wrong_index": host["nvidia"].replace("0, GPU", "1, GPU"),
        "multiple": host["nvidia"] * 2,
        "memory": host["nvidia"].replace("80000", "90000"),
        "mig_enabled": host["nvidia"].replace("Disabled", "Enabled"),
        "mig_unknown": host["nvidia"].replace("Disabled", "[Not Supported]"),
        "capability": host["nvidia"].replace("9.0", "[N/A]"),
    }
    host["nvidia"] = changes[change]
    assert "nvidia_device_invalid" in codes(probe(runtime, tmp_path))


def test_unavailable_executables_report_blockers(
    host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness.shutil, "which", lambda name: None)
    assert codes(probe(runtime, tmp_path)) == {"docker_unavailable", "nvidia_smi_unavailable"}
    assert host["calls"] == []


@pytest.mark.parametrize("stage", ["context", "info", "image"])
def test_malformed_metadata_blocked(host: dict[str, Any], runtime: RuntimeLock, tmp_path: Path, stage: str) -> None:
    host[stage] = '{"duplicate":1,"duplicate":2}'
    assert not probe(runtime, tmp_path)["ready_for_acceptance"]


def test_probe_command_timeout_is_bounded() -> None:
    result = readiness._run_command([sys.executable, "-c", "import time; time.sleep(10)"], 0.05)
    assert result.error == "probe timed out"


def test_probe_command_output_is_bounded() -> None:
    result = readiness._run_command([sys.executable, "-c", "print('x' * 300000)"], 5)
    assert result.error == "probe exceeded output limit"
    assert not result.stdout


def test_probe_command_uses_same_sanitized_routing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///local/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "ignored")
    monkeypatch.setenv("PRIVATE_CREDENTIAL", "never-pass")
    result = readiness._run_command([sys.executable, "-c", "import os,json;print(json.dumps(dict(os.environ)))"], 5)
    env = json.loads(result.stdout)
    assert env["DOCKER_HOST"] == "unix:///local/docker.sock"
    assert "DOCKER_CONTEXT" not in env
    assert "PRIVATE_CREDENTIAL" not in env


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 31])
def test_timeout_validation(runtime: RuntimeLock, tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="probe timeout"):
        probe(runtime, tmp_path, timeout_seconds=timeout)
