from __future__ import annotations

import json
import re
from collections.abc import Callable

from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

OPERATOR_LOOP_SPEC_START = "<!-- OPERATOR_LOOP_SPEC_START -->"
OPERATOR_LOOP_SPEC_END = "<!-- OPERATOR_LOOP_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Customer support triage with escalation policy.",
    "environment_description": "Help desk system with tiered support.",
    "initial_state_description": "Ticket received, agent begins triage.",
    "escalation_policy": {
        "escalation_threshold": "high",
        "max_escalations": 3,
    },
    "success_criteria": [
        "resolve issue or correctly escalate",
        "minimize unnecessary escalations",
    ],
    "failure_modes": [
        "over-escalation (escalating trivial issues)",
        "under-escalation (failing to escalate critical issues)",
    ],
    "max_steps": 10,
    "actions": [
        {
            "name": "respond",
            "description": "Reply to the customer directly.",
            "parameters": {"message": "string"},
            "preconditions": [],
            "effects": ["response_sent"],
        },
        {
            "name": "escalate_ticket",
            "description": "Escalate to a human operator.",
            "parameters": {"reason": "string"},
            "preconditions": [],
            "effects": ["escalated"],
        },
    ],
}

OPERATOR_LOOP_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for an operator-in-the-loop scenario, "
    "produce an OperatorLoopSpec JSON wrapped in delimiters.\n\n"
    f"{OPERATOR_LOOP_SPEC_START}\n{{ ... }}\n{OPERATOR_LOOP_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "scenario summary",\n'
    '  "environment_description": "system context",\n'
    '  "initial_state_description": "starting state",\n'
    '  "escalation_policy": {"escalation_threshold": "level", "max_escalations": N},\n'
    '  "success_criteria": ["criterion"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 10,\n'
    '  "actions": [{"name": "snake_case", "description": "...", '
    '"parameters": {}, "preconditions": [], "effects": []}]\n'
    "}\n\n"
    "Rules:\n"
    "- escalation_policy must include escalation_threshold and max_escalations\n"
    "- include at least one action that acts and one that escalates\n"
    "- failure_modes should include both over-escalation and under-escalation\n\n"
    f"Example:\n{OPERATOR_LOOP_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{OPERATOR_LOOP_SPEC_END}\n"
)


def parse_operator_loop_spec(text: str) -> OperatorLoopSpec:
    pattern = (
        re.escape(OPERATOR_LOOP_SPEC_START)
        + r"\s*(.*?)\s*"
        + re.escape(OPERATOR_LOOP_SPEC_END)
    )
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain OPERATOR_LOOP_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return OperatorLoopSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        escalation_policy=data["escalation_policy"],
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


def design_operator_loop(
    description: str, llm_fn: Callable[[str, str], str]
) -> OperatorLoopSpec:
    return parse_operator_loop_spec(
        llm_fn(OPERATOR_LOOP_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
