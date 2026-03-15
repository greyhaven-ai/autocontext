from __future__ import annotations

from dataclasses import dataclass, field

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class SchemaEvolutionMutationModel:
    version: int
    description: str
    breaking: bool
    fields_added: list[str] = field(default_factory=list)
    fields_removed: list[str] = field(default_factory=list)
    fields_modified: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SchemaEvolutionSpec:
    description: str
    environment_description: str
    initial_state_description: str
    mutations: list[SchemaEvolutionMutationModel]
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
