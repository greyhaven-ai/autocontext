from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from autocontext.context_bundles.runtime_evaluator import runtime_fixture_digest
from autocontext.execution.executors.local import LocalExecutor
from autocontext.execution.scenario_remote_package import (
    DEFAULT_REMOTE_RUNTIME_IMAGE,
    RemoteScenarioPackage,
    build_remote_scenario_package,
    preflight_remote_scenario_package,
)
from autocontext.execution.scenario_remote_task import build_scenario_remote_request
from autocontext.scenarios.base import ExecutionLimits
from autocontext.scenarios.grid_ctf.scenario import GridCtfScenario


def _run_package(package: RemoteScenarioPackage, tmp_path: Path) -> dict[str, object]:
    package_path = tmp_path / "scenario.pyz"
    package_path.write_bytes(package.content)
    completed = subprocess.run(
        [sys.executable, "-I", str(package_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_builtin_package_runs_on_isolated_stdlib_and_is_deterministic(tmp_path: Path) -> None:
    strategy = {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5}
    first = build_remote_scenario_package(GridCtfScenario(), strategy, 123)
    second = build_remote_scenario_package(GridCtfScenario(), strategy, 123)

    result = _run_package(first, tmp_path)

    assert first.content == second.content
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.manifest["dependencies"] == []
    assert result["replay"]["scenario"] == "grid_ctf"  # type: ignore[index]
    assert result["result"]["winner"] == "challenger"  # type: ignore[index]


def test_packaged_and_local_executors_consume_the_exact_prepared_state(tmp_path: Path) -> None:
    strategy = {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5}
    scenario = GridCtfScenario()
    prepared_state = scenario.initial_state(seed=123)
    prepared_observation = scenario.get_observation(prepared_state, "challenger")
    fixture_digest = runtime_fixture_digest(prepared_state, prepared_observation)
    expected = GridCtfScenario().execute_match_from_state(strategy, 999, prepared_state)
    assert expected.score != GridCtfScenario().execute_match(strategy, 999).score

    package = build_remote_scenario_package(
        GridCtfScenario(),
        strategy,
        999,
        initial_state=prepared_state,
        initial_observation=prepared_observation.model_dump(mode="json"),
        fixture_digest=fixture_digest,
    )
    packaged = _run_package(package, tmp_path)
    local, _ = LocalExecutor().execute_prepared_fixture(
        GridCtfScenario(),
        strategy,
        999,
        ExecutionLimits(timeout_seconds=5),
        initial_state=prepared_state,
        initial_observation=prepared_observation,
        fixture_digest=fixture_digest,
    )

    assert packaged["result"]["score"] == expected.score  # type: ignore[index]
    assert packaged["fixture_digest"] == fixture_digest
    assert local.score == expected.score


def test_custom_scenario_instance_state_is_packaged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "fixtures"))
    module = importlib.import_module("remote_custom_scenario")
    scenario = module.BiasedScenario(bias=0.25)

    package = build_remote_scenario_package(scenario, {"value": 0.5}, 7)
    result = _run_package(package, tmp_path)

    assert result["result"]["score"] == 0.75  # type: ignore[index]

    state = scenario.initial_state(seed=7)
    observation = scenario.get_observation(state, "challenger")
    fixture_digest = runtime_fixture_digest(state, observation)
    local, _ = LocalExecutor().execute_prepared_fixture(
        scenario,
        {"value": 0.5},
        7,
        ExecutionLimits(timeout_seconds=5),
        initial_state=state,
        initial_observation=observation,
        fixture_digest=fixture_digest,
    )
    assert local.score == 0.75


def test_scenario_defined_in_package_init_runs_in_clean_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "fixtures"))
    module = importlib.import_module("remote_package_scenario")

    package = build_remote_scenario_package(module.PackageScenario(), {"value": 0.75}, 7)
    result = _run_package(package, tmp_path)

    assert "remote_package_scenario/__init__.py" in package.manifest["files"]
    assert result["result"]["score"] == 0.75  # type: ignore[index]


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
    reason="set AUTOCONTEXT_RUN_DOCKER_TESTS=1 on a Docker-capable CI worker",
)
def test_custom_package_runs_in_exact_clean_network_denied_runtime_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "fixtures"))
    module = importlib.import_module("remote_custom_scenario")
    package = build_remote_scenario_package(module.BiasedScenario(bias=0.25), {"value": 0.5}, 7)
    package_path = tmp_path / "autocontext-scenario.pyz"
    package_path.write_bytes(package.content)

    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,src={package_path},dst=/task/autocontext-scenario.pyz,readonly",
            DEFAULT_REMOTE_RUNTIME_IMAGE,
            "python",
            "-I",
            "/task/autocontext-scenario.pyz",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    result = json.loads(completed.stdout)
    assert result["result"]["score"] == 0.75


def test_remote_request_uses_pinned_image_package_digest_and_denied_network() -> None:
    request = build_scenario_remote_request(
        GridCtfScenario(),
        {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        123,
        ExecutionLimits(network_access=False),
        image=DEFAULT_REMOTE_RUNTIME_IMAGE,
        cpu_cores=1,
        disk_gb=1,
    )

    assert "@sha256:" in request.image
    assert request.network_policy == "deny"
    assert request.input_artifacts[0].name == "autocontext-scenario.pyz"
    assert request.metadata["package_sha256"] == hashlib.sha256(request.input_artifacts[0].content).hexdigest()
    assert "scenario package digest mismatch" in request.command
    assert request.metadata["bootstrap_exit_code"] == "70"
    assert "fixture_digest" not in request.metadata
    assert "fixture_state_sha256" not in request.metadata
    assert "fixture_observation_sha256" not in request.metadata


def test_prepared_remote_request_binds_state_observation_and_canonical_digest() -> None:
    scenario = GridCtfScenario()
    state = scenario.initial_state(seed=123)
    observation = scenario.get_observation(state, "challenger")
    fixture_digest = runtime_fixture_digest(state, observation)

    request = build_scenario_remote_request(
        scenario,
        {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        999,
        ExecutionLimits(network_access=False),
        image=DEFAULT_REMOTE_RUNTIME_IMAGE,
        cpu_cores=1,
        disk_gb=1,
        initial_state=state,
        initial_observation=observation.model_dump(mode="json"),
        fixture_digest=fixture_digest,
    )

    assert request.metadata["fixture_digest"] == fixture_digest
    assert len(request.metadata["fixture_state_sha256"]) == 64
    assert len(request.metadata["fixture_observation_sha256"]) == 64
    with zipfile.ZipFile(io.BytesIO(request.input_artifacts[0].content)) as archive:
        payload = json.loads(archive.read("autocontext-payload.json"))
    assert payload["initial_state"] == state
    assert payload["initial_observation"] == observation.model_dump(mode="json")
    assert payload["fixture_digest"] == fixture_digest

    with pytest.raises(ValueError, match="canonical state and observation"):
        build_remote_scenario_package(
            scenario,
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            999,
            initial_state=state,
            initial_observation=observation.model_dump(mode="json"),
            fixture_digest="0" * 64,
        )


def test_package_entrypoint_fails_closed_on_embedded_digest_tamper(tmp_path: Path) -> None:
    package = build_remote_scenario_package(GridCtfScenario(), {"aggression": 0.6}, 123)
    source_path = tmp_path / "source.pyz"
    tampered_path = tmp_path / "tampered.pyz"
    source_path.write_bytes(package.content)
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(tampered_path, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "autocontext-payload.json":
                content += b" "
            target.writestr(name, content)

    completed = subprocess.run(
        [sys.executable, "-I", str(tampered_path)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 70
    assert "file digest mismatch" in completed.stderr


def test_unpinned_images_and_corrupt_packages_fail_preflight() -> None:
    with pytest.raises(ValueError, match="immutable @sha256"):
        build_scenario_remote_request(
            GridCtfScenario(),
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            123,
            ExecutionLimits(),
            image="python:3.11-slim",
            cpu_cores=1,
            disk_gb=1,
        )

    package = build_remote_scenario_package(
        GridCtfScenario(),
        {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        123,
    )
    corrupt = RemoteScenarioPackage(content=package.content + b"bad", sha256=package.sha256, manifest=package.manifest)
    with pytest.raises(ValueError, match="digest mismatch"):
        preflight_remote_scenario_package(corrupt)
