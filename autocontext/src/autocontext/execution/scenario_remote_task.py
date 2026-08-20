"""Scenario-owned hermetic packaging for the generic remote execution contract."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from autocontext.execution.remote_execution import RemoteExecutionRequest, RemoteInputArtifact, RemoteResourceRequest
from autocontext.execution.scenario_remote_package import (
    build_remote_scenario_package,
    require_pinned_runtime_image,
)
from autocontext.scenarios.base import ExecutionLimits, ScenarioInterface

_BUILTIN_SCENARIOS: dict[str, tuple[str, str]] = {
    "grid_ctf": ("autocontext.scenarios.grid_ctf.scenario", "GridCtfScenario"),
    "othello": ("autocontext.scenarios.othello", "OthelloScenario"),
}


def build_scenario_remote_request(
    scenario: ScenarioInterface,
    strategy: Mapping[str, Any],
    seed: int,
    limits: ExecutionLimits,
    *,
    image: str,
    cpu_cores: float,
    disk_gb: float,
    memory_gb: float | None = None,
) -> RemoteExecutionRequest:
    return _build_request(
        scenario=scenario,
        strategy=strategy,
        seed=seed,
        limits=limits,
        image=image,
        cpu_cores=cpu_cores,
        disk_gb=disk_gb,
        memory_gb=memory_gb,
    )


def build_builtin_scenario_remote_request(
    scenario_name: str,
    strategy: Mapping[str, Any],
    seed: int,
    limits: ExecutionLimits,
    *,
    image: str,
    cpu_cores: float,
    disk_gb: float,
    memory_gb: float | None = None,
) -> RemoteExecutionRequest:
    try:
        scenario_module, scenario_class = _BUILTIN_SCENARIOS[scenario_name]
    except KeyError as exc:
        raise ValueError(f"no packaged remote entrypoint for scenario: {scenario_name}") from exc
    module = importlib.import_module(scenario_module)
    scenario = getattr(module, scenario_class)()
    return _build_request(
        scenario=scenario,
        strategy=strategy,
        seed=seed,
        limits=limits,
        image=image,
        cpu_cores=cpu_cores,
        disk_gb=disk_gb,
        memory_gb=memory_gb,
    )


def _build_request(
    *,
    scenario: ScenarioInterface,
    strategy: Mapping[str, Any],
    seed: int,
    limits: ExecutionLimits,
    image: str,
    cpu_cores: float,
    disk_gb: float,
    memory_gb: float | None,
) -> RemoteExecutionRequest:
    require_pinned_runtime_image(image)
    package = build_remote_scenario_package(scenario, dict(strategy), seed)
    verifier = (
        "import hashlib,pathlib; "
        "content=pathlib.Path('autocontext-scenario.pyz').read_bytes(); "
        f"assert hashlib.sha256(content).hexdigest()=={package.sha256!r}, 'scenario package digest mismatch'"
    )
    limit_memory_gb = max(0.25, float(limits.max_memory_mb) / 1024.0)
    requested_memory_gb = min(memory_gb, limit_memory_gb) if memory_gb is not None else limit_memory_gb
    return RemoteExecutionRequest(
        task_id=f"scenario:{scenario.name}:{seed}",
        image=image,
        command=f"set -eu\npython -c {verifier!r} || exit 70\npython autocontext-scenario.pyz",
        resources=RemoteResourceRequest(
            cpu_cores=cpu_cores,
            memory_gb=requested_memory_gb,
            disk_gb=disk_gb,
        ),
        timeout_seconds=limits.timeout_seconds,
        network_policy="allow" if limits.network_access else "deny",
        input_artifacts=(
            RemoteInputArtifact(
                "autocontext-scenario.pyz",
                package.content,
                "application/vnd.autocontext.scenario+zip",
            ),
        ),
        metadata={
            "scenario": scenario.name,
            "seed": str(seed),
            "task_kind": "scenario_match",
            "package_format": str(package.manifest["format"]),
            "package_sha256": package.sha256,
            "runtime": str(package.manifest["runtime"]),
            "bootstrap_exit_code": "70",
        },
    )


__all__ = ["build_builtin_scenario_remote_request", "build_scenario_remote_request"]
