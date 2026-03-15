from __future__ import annotations

from dataclasses import dataclass

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class InvestigationSpec:
    description: str
    environment_description: str
    initial_state_description: str
    evidence_pool_description: str
    diagnosis_target: str
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
