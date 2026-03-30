from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.schema_evolution_spec import (
    SchemaEvolutionMutationModel,
    SchemaEvolutionSpec,
)
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

SCHEMA_EVOLUTION_SPEC_START = "<!-- SCHEMA_EVOLUTION_SPEC_START -->"
SCHEMA_EVOLUTION_SPEC_END = "<!-- SCHEMA_EVOLUTION_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "API schema evolves from v1 to v3 during a data migration task.",
    "environment_description": "REST API backend with versioned schemas.",
    "initial_state_description": "v1 schema is active; all endpoints respond with v1 format.",
    "mutations": [
        {
            "version": 2,
            "description": "Add 'priority' field to task objects.",
            "breaking": False,
            "fields_added": ["priority"],
            "fields_removed": [],
            "fields_modified": {},
        },
        {
            "version": 3,
            "description": "Rename 'status' to 'state' and remove 'legacy_id'.",
            "breaking": True,
            "fields_added": ["state"],
            "fields_removed": ["status", "legacy_id"],
            "fields_modified": {},
        },
    ],
    "success_criteria": [
        "detect each schema version change",
        "discard stale assumptions about removed fields",
    ],
    "failure_modes": ["using removed fields after mutation", "caching stale schema"],
    "max_steps": 8,
    "actions": [
        {
            "name": "query_api",
            "description": "Query an API endpoint and observe the response schema.",
            "parameters": {"endpoint": "string"},
            "preconditions": [],
            "effects": ["schema_observed"],
        },
        {
            "name": "validate_schema",
            "description": "Check whether the current schema matches expectations.",
            "parameters": {},
            "preconditions": ["query_api"],
            "effects": ["schema_validated"],
        },
    ],
}

SCHEMA_EVOLUTION_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a schema-evolution or stale-context scenario, "
    "produce a SchemaEvolutionSpec JSON wrapped in delimiters.\n\n"
    f"{SCHEMA_EVOLUTION_SPEC_START}\n{{ ... }}\n{SCHEMA_EVOLUTION_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "scenario summary",\n'
    '  "environment_description": "what system has evolving schemas",\n'
    '  "initial_state_description": "starting state with initial schema version",\n'
    '  "mutations": [\n'
    "    {\n"
    '      "version": 2,\n'
    '      "description": "what changed",\n'
    '      "breaking": true,\n'
    '      "fields_added": ["field"],\n'
    '      "fields_removed": ["field"],\n'
    '      "fields_modified": {"field": "old_type -> new_type"}\n'
    "    }\n"
    "  ],\n"
    '  "success_criteria": ["criterion"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 8,\n'
    '  "actions": [\n'
    "    {\n"
    '      "name": "snake_case",\n'
    '      "description": "what the action does",\n'
    '      "parameters": {"param": "type"},\n'
    '      "preconditions": [],\n'
    '      "effects": ["effect"]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- include at least one breaking mutation\n"
    "- model the scenario around detecting and adapting to schema changes\n"
    "- include at least two actions and two mutations\n\n"
    f"Example:\n{SCHEMA_EVOLUTION_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{SCHEMA_EVOLUTION_SPEC_END}\n"
)


def parse_schema_evolution_spec(text: str) -> SchemaEvolutionSpec:
    pattern = re.escape(SCHEMA_EVOLUTION_SPEC_START) + r"\s*(.*?)\s*" + re.escape(SCHEMA_EVOLUTION_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain SCHEMA_EVOLUTION_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return SchemaEvolutionSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        mutations=[
            SchemaEvolutionMutationModel(
                version=m["version"],
                description=m["description"],
                breaking=m["breaking"],
                fields_added=m.get("fields_added", []),
                fields_removed=m.get("fields_removed", []),
                fields_modified=m.get("fields_modified", {}),
            )
            for m in data["mutations"]
        ],
        success_criteria=data["success_criteria"],
        failure_modes=data.get("failure_modes", []),
        actions=[
            SimulationActionSpecModel(
                name=raw["name"],
                description=raw["description"],
                parameters=raw.get("parameters", {}),
                preconditions=raw.get("preconditions", []),
                effects=raw.get("effects", []),
            )
            for raw in data["actions"]
        ],
        max_steps=data.get("max_steps", 10),
    )


def design_schema_evolution(description: str, llm_fn: LlmFn) -> SchemaEvolutionSpec:
    return parse_schema_evolution_spec(
        llm_fn(SCHEMA_EVOLUTION_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
