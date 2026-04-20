"""Shared helpers for the simulation engine."""

from __future__ import annotations

import inspect
import logging
import re
import types
from typing import Any, cast

from autocontext.agents.types import LlmFn

logger = logging.getLogger(__name__)

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
        if family in {"operator_loop", "schema_evolution"}:
            return family
        return "simulation"
    except Exception:
        return "simulation"


def design_structured_family_spec(description: str, family: str, llm_fn: LlmFn) -> dict[str, Any] | None:
    from autocontext.scenarios.custom.generic_creator import spec_to_plain_data

    if family == "operator_loop":
        from autocontext.scenarios.custom.operator_loop_designer import design_operator_loop

        try:
            plain = spec_to_plain_data(design_operator_loop(description, llm_fn))
        except Exception:
            logger.debug("simulation.helpers: operator_loop designer fallback", exc_info=True)
        else:
            if isinstance(plain, dict):
                return cast(dict[str, Any], plain)

    if family == "schema_evolution":
        from autocontext.scenarios.custom.schema_evolution_designer import design_schema_evolution

        try:
            plain = spec_to_plain_data(design_schema_evolution(description, llm_fn))
        except Exception:
            logger.debug("simulation.helpers: schema_evolution designer fallback", exc_info=True)
        else:
            if isinstance(plain, dict):
                return cast(dict[str, Any], plain)

    if family == "simulation":
        from autocontext.scenarios.custom.simulation_designer import design_simulation

        try:
            plain = spec_to_plain_data(design_simulation(description, llm_fn))
        except Exception:
            logger.debug("simulation.helpers: simulation designer fallback", exc_info=True)
        else:
            if isinstance(plain, dict):
                return cast(dict[str, Any], plain)

    return None


def build_json_spec_prompt(family: str) -> str:
    if family == "schema_evolution":
        return (
            "You are a schema-evolution designer. Produce a schema_evolution spec as JSON.\n"
            "Required: description, environment_description, initial_state_description, mutations, "
            "success_criteria, failure_modes, max_steps, actions.\n"
            "Output ONLY JSON."
        )

    return (
        f"You are a simulation designer. Produce a {family} spec as JSON.\n"
        "Required: description, environment_description, initial_state_description, "
        "success_criteria, failure_modes, max_steps, actions.\n"
        "Output ONLY JSON."
    )


def fallback_spec_for_family(description: str, family: str) -> dict[str, Any]:
    if family == "schema_evolution":
        return {
            "description": description,
            "environment_description": "Versioned system with evolving schema.",
            "initial_state_description": "Schema v1 is active.",
            "mutations": [
                {
                    "version": 2,
                    "description": "Add a new required field.",
                    "breaking": True,
                    "fields_added": ["new_field"],
                    "fields_removed": [],
                    "fields_modified": {},
                }
            ],
            "success_criteria": ["detect schema change", "adapt to new version"],
            "failure_modes": ["stale assumptions after mutation"],
            "max_steps": 10,
            "actions": [
                {
                    "name": "observe_schema",
                    "description": "Observe the current schema.",
                    "parameters": {},
                    "preconditions": [],
                    "effects": ["schema_observed"],
                },
                {
                    "name": "adapt_to_mutation",
                    "description": "Adapt once the schema changes.",
                    "parameters": {},
                    "preconditions": ["observe_schema"],
                    "effects": ["schema_adapted"],
                },
            ],
        }

    return {
        "description": description,
        "environment_description": "Simulated environment",
        "initial_state_description": "Initial state",
        "success_criteria": ["achieve objective"],
        "failure_modes": ["timeout"],
        "max_steps": 10,
        "actions": [{"name": "act", "description": "Take action", "parameters": {}, "preconditions": [], "effects": []}],
    }


def generate_source_for_family(spec: dict[str, Any], name: str, family: str) -> str:
    from autocontext.scenarios.custom.simulation_spec import parse_simulation_actions

    if family == "operator_loop":
        from autocontext.scenarios.custom.operator_loop_codegen import generate_operator_loop_class
        from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec

        ol_spec = OperatorLoopSpec(
            description=spec.get("description", ""),
            environment_description=spec.get("environment_description", ""),
            initial_state_description=spec.get("initial_state_description", ""),
            escalation_policy=spec.get("escalation_policy", {"escalation_threshold": "medium", "max_escalations": 5}),
            success_criteria=spec.get("success_criteria", []),
            failure_modes=spec.get("failure_modes", []),
            actions=parse_simulation_actions(spec.get("actions", [])),
            max_steps=spec.get("max_steps", 10),
        )
        return generate_operator_loop_class(ol_spec, name)

    if family == "schema_evolution":
        from autocontext.scenarios.custom.schema_evolution_codegen import generate_schema_evolution_class
        from autocontext.scenarios.custom.schema_evolution_spec import (
            SchemaEvolutionMutationModel,
            SchemaEvolutionSpec,
        )

        schema_spec = SchemaEvolutionSpec(
            description=spec.get("description", ""),
            environment_description=spec.get("environment_description", ""),
            initial_state_description=spec.get("initial_state_description", ""),
            mutations=[
                SchemaEvolutionMutationModel(
                    version=int(mutation.get("version", 1)),
                    description=str(mutation.get("description", "")),
                    breaking=bool(mutation.get("breaking", False)),
                    fields_added=list(mutation.get("fields_added", [])),
                    fields_removed=list(mutation.get("fields_removed", [])),
                    fields_modified=dict(mutation.get("fields_modified", {})),
                )
                for mutation in spec.get("mutations", [])
                if isinstance(mutation, dict)
            ],
            success_criteria=spec.get("success_criteria", []),
            failure_modes=spec.get("failure_modes", []),
            actions=parse_simulation_actions(spec.get("actions", [])),
            max_steps=spec.get("max_steps", 10),
        )
        return generate_schema_evolution_class(schema_spec, name)

    from autocontext.scenarios.custom.simulation_codegen import generate_simulation_class
    from autocontext.scenarios.custom.simulation_spec import SimulationSpec

    sim_spec = SimulationSpec(
        description=spec.get("description", ""),
        environment_description=spec.get("environment_description", ""),
        initial_state_description=spec.get("initial_state_description", ""),
        success_criteria=spec.get("success_criteria", []),
        failure_modes=spec.get("failure_modes", []),
        actions=parse_simulation_actions(spec.get("actions", [])),
        max_steps=spec.get("max_steps", 10),
    )
    return generate_simulation_class(sim_spec, name)


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
