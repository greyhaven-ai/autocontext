from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.coordination_spec import CoordinationSpec
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

COORDINATION_SPEC_START = "<!-- COORDINATION_SPEC_START -->"
COORDINATION_SPEC_END = "<!-- COORDINATION_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Multi-agent research report writing.",
    "environment_description": "Research team with partial information.",
    "initial_state_description": "Task partitioned across workers.",
    "workers": [
        {"worker_id": "researcher", "role": "data gatherer"},
        {"worker_id": "writer", "role": "report writer"},
    ],
    "success_criteria": [
        "coherent merged report",
        "minimal duplication across sections",
    ],
    "failure_modes": [
        "duplicate content across workers",
        "lost information during handoff",
    ],
    "max_steps": 10,
    "actions": [
        {
            "name": "research",
            "description": "Gather data on assigned topic.",
            "parameters": {"topic": "string"},
            "preconditions": [],
            "effects": ["data_gathered"],
        },
        {
            "name": "write_section",
            "description": "Write a report section.",
            "parameters": {"section": "string"},
            "preconditions": ["research"],
            "effects": ["section_written"],
        },
    ],
}

COORDINATION_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a multi-agent coordination scenario, "
    "produce a CoordinationSpec JSON wrapped in delimiters.\n\n"
    f"{COORDINATION_SPEC_START}\n{{ ... }}\n{COORDINATION_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "scenario summary",\n'
    '  "environment_description": "team context",\n'
    '  "initial_state_description": "starting state",\n'
    '  "workers": [{"worker_id": "name", "role": "role"}],\n'
    '  "success_criteria": ["criterion"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 10,\n'
    '  "actions": [{"name": "snake_case", "description": "...", '
    '"parameters": {}, "preconditions": [], "effects": []}]\n'
    "}\n\n"
    "Rules:\n"
    "- include at least two workers with distinct roles\n"
    "- workers do not share full context by default\n"
    "- include at least two actions\n\n"
    f"Example:\n{COORDINATION_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{COORDINATION_SPEC_END}\n"
)


def parse_coordination_spec(text: str) -> CoordinationSpec:
    pattern = (
        re.escape(COORDINATION_SPEC_START)
        + r"\s*(.*?)\s*"
        + re.escape(COORDINATION_SPEC_END)
    )
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain COORDINATION_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return CoordinationSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        workers=data["workers"],
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


def design_coordination(
    description: str, llm_fn: LlmFn
) -> CoordinationSpec:
    return parse_coordination_spec(
        llm_fn(COORDINATION_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
