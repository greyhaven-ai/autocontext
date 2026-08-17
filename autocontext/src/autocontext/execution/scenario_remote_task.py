"""Scenario-owned packaging for the generic remote execution contract."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from autocontext.execution.remote_execution import RemoteExecutionRequest, RemoteResourceRequest
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
        scenario_name=scenario.name,
        scenario_module=scenario.__class__.__module__,
        scenario_class=scenario.__class__.__name__,
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
    return _build_request(
        scenario_name=scenario_name,
        scenario_module=scenario_module,
        scenario_class=scenario_class,
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
    scenario_name: str,
    scenario_module: str,
    scenario_class: str,
    strategy: Mapping[str, Any],
    seed: int,
    limits: ExecutionLimits,
    image: str,
    cpu_cores: float,
    disk_gb: float,
    memory_gb: float | None,
) -> RemoteExecutionRequest:
    payload = {
        "scenario_name": scenario_name,
        "scenario_module": scenario_module,
        "scenario_class": scenario_class,
        "strategy": dict(strategy),
        "seed": seed,
    }
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    script = f"""import base64
import importlib
import json

payload = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
module = importlib.import_module(payload["scenario_module"])
scenario = getattr(module, payload["scenario_class"])()
result = scenario.execute_match(payload["strategy"], int(payload["seed"]))
replay = {{
    "scenario": scenario.name,
    "seed": int(payload["seed"]),
    "narrative": scenario.replay_to_narrative(result.replay),
    "timeline": result.replay,
}}
print(json.dumps({{"result": result.model_dump(mode="json"), "replay": replay}}))
"""
    limit_memory_gb = max(0.25, float(limits.max_memory_mb) / 1024.0)
    requested_memory_gb = min(memory_gb, limit_memory_gb) if memory_gb is not None else limit_memory_gb
    return RemoteExecutionRequest(
        task_id=f"scenario:{scenario_name}:{seed}",
        image=image,
        command="python - <<'PY'\n" + script + "\nPY",
        resources=RemoteResourceRequest(
            cpu_cores=cpu_cores,
            memory_gb=requested_memory_gb,
            disk_gb=disk_gb,
        ),
        timeout_seconds=limits.timeout_seconds,
        network_policy="allow" if limits.network_access else "deny",
        metadata={"scenario": scenario_name, "seed": str(seed), "task_kind": "scenario_match"},
    )


__all__ = ["build_builtin_scenario_remote_request", "build_scenario_remote_request"]
