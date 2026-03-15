from __future__ import annotations

from dataclasses import dataclass

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class WorkflowStepSpecModel:
    name: str
    description: str
    idempotent: bool
    reversible: bool
    compensation: str | None = None


@dataclass(slots=True)
class WorkflowSpec:
    description: str
    environment_description: str
    initial_state_description: str
    workflow_steps: list[WorkflowStepSpecModel]
    success_criteria: list[str]
    actions: list[SimulationActionSpecModel]
    failure_modes: list[str]
    max_steps: int = 10
