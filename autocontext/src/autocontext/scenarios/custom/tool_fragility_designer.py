from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
from autocontext.scenarios.custom.tool_fragility_spec import (
    ToolContractSpecModel,
    ToolFragilitySpec,
)

TOOL_FRAGILITY_SPEC_START = "<!-- TOOL_FRAGILITY_SPEC_START -->"
TOOL_FRAGILITY_SPEC_END = "<!-- TOOL_FRAGILITY_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "API contracts drift during a data processing pipeline.",
    "environment_description": "Microservice architecture with versioned API contracts.",
    "initial_state_description": "All tools at v1; pipeline runs successfully.",
    "tool_contracts": [
        {"tool_name": "search_api", "version": 1, "description": "Search endpoint returning flat list."},
        {"tool_name": "transform_api", "version": 1, "description": "Data transformation endpoint."},
    ],
    "success_criteria": [
        "complete the pipeline despite tool changes",
        "detect and adapt to changed response formats",
    ],
    "failure_modes": ["using stale response format", "selecting wrong tool"],
    "max_steps": 10,
    "actions": [
        {
            "name": "call_search",
            "description": "Call the search API with a query.",
            "parameters": {"query": "string"},
            "preconditions": [],
            "effects": ["search_results_obtained"],
        },
        {
            "name": "call_transform",
            "description": "Transform data using the transformation API.",
            "parameters": {"data": "string"},
            "preconditions": ["call_search"],
            "effects": ["data_transformed"],
        },
    ],
}

TOOL_FRAGILITY_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a tool-fragility or environment-drift scenario, "
    "produce a ToolFragilitySpec JSON wrapped in delimiters.\n\n"
    f"{TOOL_FRAGILITY_SPEC_START}\n{{ ... }}\n{TOOL_FRAGILITY_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "scenario summary",\n'
    '  "environment_description": "what system has drifting tools",\n'
    '  "initial_state_description": "starting state with stable tools",\n'
    '  "tool_contracts": [\n'
    "    {\n"
    '      "tool_name": "api_name",\n'
    '      "version": 1,\n'
    '      "description": "what this tool does"\n'
    "    }\n"
    "  ],\n"
    '  "success_criteria": ["criterion"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 10,\n'
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
    "- include at least two tool contracts\n"
    "- model the scenario around adapting to changed tool behavior\n"
    "- include at least two actions\n\n"
    f"Example:\n{TOOL_FRAGILITY_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{TOOL_FRAGILITY_SPEC_END}\n"
)


def parse_tool_fragility_spec(text: str) -> ToolFragilitySpec:
    pattern = re.escape(TOOL_FRAGILITY_SPEC_START) + r"\s*(.*?)\s*" + re.escape(TOOL_FRAGILITY_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain TOOL_FRAGILITY_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return ToolFragilitySpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        tool_contracts=[
            ToolContractSpecModel(
                tool_name=tc["tool_name"],
                version=tc["version"],
                description=tc["description"],
            )
            for tc in data["tool_contracts"]
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


def design_tool_fragility(description: str, llm_fn: LlmFn) -> ToolFragilitySpec:
    return parse_tool_fragility_spec(
        llm_fn(TOOL_FRAGILITY_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
