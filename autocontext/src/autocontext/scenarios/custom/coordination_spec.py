from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class CoordinationSpec:
    """Spec for a multi-agent coordination scenario."""

    description: str
    environment_description: str
    initial_state_description: str
    workers: list[dict[str, Any]]  # [{worker_id, role, ...}]
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
