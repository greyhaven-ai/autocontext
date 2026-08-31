"""Build interactive-run environment metadata without server lifecycle state."""

from __future__ import annotations

from typing import Any

from autocontext.config import AppSettings
from autocontext.config.production_execution import parse_csv_values
from autocontext.scenarios import SCENARIO_REGISTRY

_BUILTIN_SCENARIO_DESCRIPTIONS = {
    "grid_ctf": (
        "20x20 capture-the-flag map with fog of war and three unit archetypes "
        "(Scout, Soldier, Commander). Preserve at least one defender near base."
    ),
    "othello": (
        "Standard Othello opening phase on an 8x8 board. Valid actions optimize "
        "mobility and corner pressure."
    ),
}


def build_run_environment_info(settings: AppSettings) -> dict[str, Any]:
    # This catalog is reachable with read/control capabilities. Scenario
    # constructors and describe hooks may contain host-installed project code,
    # so metadata reads must never invoke them. Custom scenarios retain their
    # registry name and are resolved only by host-authorized run operations.
    scenario_names = sorted(name for name in SCENARIO_REGISTRY if type(name) is str)
    scenarios = [
        {
            "name": name,
            "description": _BUILTIN_SCENARIO_DESCRIPTIONS.get(name, name),
        }
        for name in scenario_names
    ]

    configured = bool(settings.primeintellect_api_key)
    accelerator_kind = settings.primeintellect_accelerator_kind.strip()
    required_telemetry = (
        sorted(parse_csv_values(settings.primeintellect_required_telemetry))
        if accelerator_kind
        else []
    )
    executors: list[dict[str, Any]] = [
        {
            "mode": "local",
            "available": True,
            "description": "Local process execution with sandbox isolation",
        },
        {
            "mode": "primeintellect",
            "available": configured,
            "description": "Remote execution via PrimeIntellect sandbox API",
            "resources": {
                "docker_image": settings.primeintellect_docker_image,
                "cpu_cores": settings.primeintellect_cpu_cores,
                "memory_gb": settings.primeintellect_memory_gb,
                "disk_gb": settings.primeintellect_disk_size_gb,
                "timeout_minutes": settings.primeintellect_timeout_minutes,
                "accelerator": (
                    {
                        "kind": accelerator_kind,
                        "count": settings.primeintellect_accelerator_count,
                    }
                    if accelerator_kind
                    else None
                ),
                "region": settings.primeintellect_region.strip() or None,
                "required_telemetry": required_telemetry,
            },
        },
    ]
    return {
        "scenarios": scenarios,
        "executors": executors,
        "current_executor": settings.executor_mode,
        "agent_provider": settings.agent_provider,
    }
