from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel


@dataclass(slots=True)
class NegotiationSpec:
    """Spec for a negotiation scenario."""

    description: str
    environment_description: str
    initial_state_description: str
    hidden_preferences: dict[str, Any]  # priorities, reservation, aspiration, batna
    max_rounds: int
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 0  # auto-derived from max_rounds * 2 if not set

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            self.max_steps = max(self.max_rounds * 2, 4)
