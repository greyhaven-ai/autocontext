"""Shared helpers for the simulation engine."""

from __future__ import annotations

import inspect
import re
import types
from typing import Any

# Only explicit human-oversight / clarification semantics should short-circuit to
# operator_loop here. Broader escalation language belongs to the family classifier,
# which can still route geopolitical and statecraft prompts to simulation.
_OPERATOR_LOOP_FAMILY_TRIGGERS = re.compile(
    r"operator|human[- .]?in[- .]?the[- .]?loop|clarif|approval.required|"
    r"ambiguous.support|incomplete input|ask.*question|missing.information|gather.more.info"
)

_STATECRAFT_SIMULATION_CONTEXT = re.compile(
    r"geopolit|statecraft|national security|international crisis|international confrontation|"
    r"crisis wargame|hybrid warfare|military movements|cyber-kinetic"
)


def find_scenario_class(mod: types.ModuleType) -> type | None:
    """Find the first concrete generated scenario class in a module."""
    from autocontext.scenarios.simulation import SimulationInterface

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, SimulationInterface)
            and attr is not SimulationInterface
            and not inspect.isabstract(attr)
        ):
            return attr

    try:
        from autocontext.scenarios.operator_loop import OperatorLoopInterface
    except ImportError:
        return None

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, OperatorLoopInterface)
            and attr is not OperatorLoopInterface
            and not inspect.isabstract(attr)
        ):
            return attr

    return None


def infer_family(description: str) -> str:
    text_lower = description.lower()

    if _OPERATOR_LOOP_FAMILY_TRIGGERS.search(text_lower):
        return "operator_loop"

    try:
        from autocontext.scenarios.custom.family_classifier import (
            classify_scenario_family,
            route_to_family,
        )

        family = route_to_family(classify_scenario_family(description), 0.15).name
        if family == "operator_loop" and _STATECRAFT_SIMULATION_CONTEXT.search(text_lower):
            return "simulation"
        return "operator_loop" if family == "operator_loop" else "simulation"
    except Exception:
        return "simulation"


def aggregate_contract_signal_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    aggregate: dict[str, int] = {}
    for key in ("escalation_count", "clarification_count"):
        counts = [value for value in (result.get(key) for result in results) if isinstance(value, int | float)]
        if counts:
            aggregate[key] = int(sum(counts))
    return aggregate


def apply_behavioral_contract(
    *,
    description: str,
    family: str,
    summary: dict[str, Any],
    warnings: list[str],
) -> tuple[str, list[str]]:
    from autocontext.scenarios.family_contracts import get_family_contract

    contract = get_family_contract(family)
    if contract is None:
        return "completed", []

    contract_result = contract.evaluate(description, summary)
    warnings.extend(contract_result.warnings)
    if contract_result.satisfied:
        return "completed", []

    if contract_result.score_ceiling is not None:
        summary["score"] = min(summary.get("score", 0), contract_result.score_ceiling)
    warnings.append(contract_result.reason)
    return "incomplete", contract_result.missing_signals
