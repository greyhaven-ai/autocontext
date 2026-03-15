from __future__ import annotations

from dataclasses import dataclass

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class ToolContractSpecModel:
    tool_name: str
    version: int
    description: str


@dataclass(slots=True)
class ToolFragilitySpec:
    description: str
    environment_description: str
    initial_state_description: str
    tool_contracts: list[ToolContractSpecModel]
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
