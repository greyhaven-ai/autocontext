"""pure policy functions over the charter: the autonomy dial and budget windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from autocontext.ambient.charter import AutonomyLevel, Charter, CharterBudgets

Action = Literal["train", "promote"]


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


def effective_autonomy(charter: Charter, target_name: str) -> AutonomyLevel:
    for target in charter.targets:
        if target.name == target_name:
            return target.autonomy or charter.autonomy
    raise KeyError(f"unknown charter target: {target_name}")


def decide(charter: Charter, action: Action, target_name: str) -> PolicyDecision:
    autonomy = effective_autonomy(charter, target_name)
    if autonomy == "propose":
        return PolicyDecision(True, True, f"autonomy=propose: {action} requires approval")
    if autonomy == "train":
        if action == "train":
            return PolicyDecision(True, False, "autonomy=train: training is autonomous")
        return PolicyDecision(True, True, "autonomy=train: promotion requires approval")
    return PolicyDecision(True, False, f"autonomy=full: {action} is autonomous within budgets")


def budget_allows(budgets: CharterBudgets, used_gpu_hours_in_window: float, requested_gpu_hours: float) -> bool:
    return used_gpu_hours_in_window + requested_gpu_hours <= budgets.gpu_hours_per_window
