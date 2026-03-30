from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel, SimulationSpec

SIM_SPEC_START = "<!-- SIMULATION_SPEC_START -->"
SIM_SPEC_END = "<!-- SIMULATION_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Recover a multi-step API workflow after a mid-flow cancellation.",
    "environment_description": "Mock booking system with flight, hotel, and transport dependencies.",
    "initial_state_description": "No bookings yet. APIs are healthy. Cancellation can occur mid-flow.",
    "success_criteria": [
        "flight, hotel, and transport are all booked consistently",
        "if a booking fails mid-flow, the agent either compensates or rolls back cleanly",
    ],
    "failure_modes": ["flight cancellation", "booking dependency mismatch", "partial side effects left behind"],
    "max_steps": 8,
    "actions": [
        {
            "name": "book_flight",
            "description": "Reserve a flight that satisfies user constraints.",
            "parameters": {"flight_id": "string"},
            "preconditions": [],
            "effects": ["flight_reserved"],
        },
        {
            "name": "book_hotel",
            "description": "Reserve a hotel matched to the trip dates.",
            "parameters": {"hotel_id": "string"},
            "preconditions": ["book_flight"],
            "effects": ["hotel_reserved"],
        },
    ],
}

SIMULATION_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a stateful or action-trace task, "
    "produce a SimulationSpec JSON wrapped in delimiters.\n\n"
    f"{SIM_SPEC_START}\n{{ ... }}\n{SIM_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "human readable scenario summary",\n'
    '  "environment_description": "what the mock environment models",\n'
    '  "initial_state_description": "starting state narrative",\n'
    '  "success_criteria": ["condition 1", "condition 2"],\n'
    '  "failure_modes": ["failure 1"],\n'
    '  "max_steps": 8,\n'
    '  "actions": [\n'
    "    {\n"
    '      "name": "action_name",\n'
    '      "description": "what the action does",\n'
    '      "parameters": {"param": "type"},\n'
    '      "preconditions": ["previous_action_name"],\n'
    '      "effects": ["effect description"]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- model the task as an environment with explicit actions and dependencies\n"
    "- use action names that are short, stable, and snake_case\n"
    "- use preconditions to represent valid ordering constraints\n"
    "- include failure modes and at least two success criteria\n"
    "- keep the action set minimal but sufficient to complete and recover the workflow\n\n"
    f"Example:\n{SIM_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{SIM_SPEC_END}\n"
)


def parse_simulation_spec(text: str) -> SimulationSpec:
    pattern = re.escape(SIM_SPEC_START) + r"\s*(.*?)\s*" + re.escape(SIM_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain SIMULATION_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return SimulationSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
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


def design_simulation(description: str, llm_fn: LlmFn) -> SimulationSpec:
    return parse_simulation_spec(llm_fn(SIMULATION_DESIGNER_SYSTEM, f"User description:\n{description}"))
