from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SimulationActionSpecModel:
    name: str
    description: str
    parameters: dict[str, str]
    preconditions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SimulationSpec:
    description: str
    environment_description: str
    initial_state_description: str
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
