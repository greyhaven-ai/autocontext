"""Schema-evolution helpers for simulation-generated scenarios."""

from __future__ import annotations

import logging
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.schema_evolution_codegen import generate_schema_evolution_class
from autocontext.scenarios.custom.schema_evolution_designer import design_schema_evolution
from autocontext.scenarios.custom.schema_evolution_spec import (
    SchemaEvolutionMutationModel,
    SchemaEvolutionSpec,
)
from autocontext.scenarios.custom.simulation_spec import (
    SimulationActionSpecModel,
    normalize_simulation_spec_dict,
    parse_simulation_actions,
)

logger = logging.getLogger(__name__)


def design_spec(description: str, llm_fn: LlmFn) -> dict[str, Any] | None:
    try:
        return _spec_to_dict(design_schema_evolution(description, llm_fn))
    except Exception:
        logger.debug("simulation.schema_evolution: designer fallback", exc_info=True)
        return None


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_simulation_spec_dict(spec)
    normalized["mutations"] = _normalize_mutations(spec.get("mutations"))
    return normalized


def generate_source(spec: dict[str, Any], name: str) -> str:
    return generate_schema_evolution_class(_spec_from_dict(spec), name)


def _spec_to_dict(spec: SchemaEvolutionSpec) -> dict[str, Any]:
    return {
        "description": spec.description,
        "environment_description": spec.environment_description,
        "initial_state_description": spec.initial_state_description,
        "mutations": [_mutation_to_dict(mutation) for mutation in spec.mutations],
        "success_criteria": list(spec.success_criteria),
        "failure_modes": list(spec.failure_modes),
        "actions": [_action_to_dict(action) for action in spec.actions],
        "max_steps": spec.max_steps,
    }


def _spec_from_dict(spec: dict[str, Any]) -> SchemaEvolutionSpec:
    return SchemaEvolutionSpec(
        description=str(spec.get("description") or ""),
        environment_description=str(spec.get("environment_description") or "Schema-evolution environment"),
        initial_state_description=str(spec.get("initial_state_description") or "Initial schema version is active."),
        mutations=[
            SchemaEvolutionMutationModel(
                version=int(mutation["version"]),
                description=str(mutation["description"]),
                breaking=bool(mutation["breaking"]),
                fields_added=list(mutation.get("fields_added", [])),
                fields_removed=list(mutation.get("fields_removed", [])),
                fields_modified=dict(mutation.get("fields_modified", {})),
            )
            for mutation in _normalize_mutations(spec.get("mutations"))
        ],
        success_criteria=[str(item) for item in spec.get("success_criteria", [])],
        failure_modes=[str(item) for item in spec.get("failure_modes", [])],
        actions=parse_simulation_actions(spec.get("actions", [])),
        max_steps=int(spec.get("max_steps") or 10),
    )


def _normalize_mutations(raw: Any) -> list[dict[str, Any]]:
    mutations: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw, start=2):
            if not isinstance(item, dict):
                continue
            fields_modified = item.get("fields_modified", {})
            mutations.append({
                "version": int(item.get("version") or index),
                "description": str(item.get("description") or f"Schema version {index} mutation"),
                "breaking": bool(item.get("breaking", False)),
                "fields_added": _text_list(item.get("fields_added")),
                "fields_removed": _text_list(item.get("fields_removed")),
                "fields_modified": {
                    str(field): str(change)
                    for field, change in (fields_modified.items() if isinstance(fields_modified, dict) else [])
                },
            })
    if mutations:
        return mutations
    return [{
        "version": 2,
        "description": "Schema changes during the run and invalidates stale assumptions.",
        "breaking": True,
        "fields_added": ["schema_version"],
        "fields_removed": ["legacy_status"],
        "fields_modified": {"risk_model_assumptions": "v1 -> v2"},
    }]


def _mutation_to_dict(mutation: SchemaEvolutionMutationModel) -> dict[str, Any]:
    return {
        "version": mutation.version,
        "description": mutation.description,
        "breaking": mutation.breaking,
        "fields_added": list(mutation.fields_added),
        "fields_removed": list(mutation.fields_removed),
        "fields_modified": dict(mutation.fields_modified),
    }


def _action_to_dict(action: SimulationActionSpecModel) -> dict[str, Any]:
    return action.to_dict()


def _text_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []
