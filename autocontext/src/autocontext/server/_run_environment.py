"""Build interactive-run environment metadata without server lifecycle state."""

from __future__ import annotations

from typing import Any

from autocontext.config import AppSettings
from autocontext.config.production_execution import parse_csv_values
from autocontext.scenarios import SCENARIO_REGISTRY


def build_run_environment_info(settings: AppSettings) -> dict[str, Any]:
    scenarios: list[dict[str, str]] = []
    for name in sorted(SCENARIO_REGISTRY):
        instance = SCENARIO_REGISTRY[name]()
        if hasattr(instance, "describe_rules"):
            description = instance.describe_rules()
        elif hasattr(instance, "describe_task"):
            description = instance.describe_task()
        else:
            description = name
        scenarios.append({"name": name, "description": description})

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
