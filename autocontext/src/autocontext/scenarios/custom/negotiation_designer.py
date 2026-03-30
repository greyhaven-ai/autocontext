from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.negotiation_spec import NegotiationSpec
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

NEGOTIATION_SPEC_START = "<!-- NEGOTIATION_SPEC_START -->"
NEGOTIATION_SPEC_END = "<!-- NEGOTIATION_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Contract price negotiation with hidden BATNA.",
    "environment_description": "Buyer-seller negotiation over contract terms.",
    "initial_state_description": "Both parties have opening positions; hidden preferences unknown.",
    "hidden_preferences": {
        "priorities": {"price": 0.6, "delivery_time": 0.3, "warranty": 0.1},
        "reservation_value": 50.0,
        "aspiration_value": 85.0,
        "batna_description": "Switch to alternative vendor with longer lead time.",
    },
    "max_rounds": 5,
    "success_criteria": [
        "reach agreement above reservation value",
        "accurately model opponent priorities by final round",
    ],
    "failure_modes": ["deadlock without agreement", "accept below BATNA"],
    "actions": [
        {
            "name": "make_offer",
            "description": "Propose contract terms to the opponent.",
            "parameters": {"terms": "dict"},
            "preconditions": [],
            "effects": ["offer_on_table"],
        },
        {
            "name": "counter_offer",
            "description": "Respond with modified terms.",
            "parameters": {"terms": "dict"},
            "preconditions": ["make_offer"],
            "effects": ["counter_on_table"],
        },
        {
            "name": "accept",
            "description": "Accept the current terms on the table.",
            "parameters": {},
            "preconditions": ["make_offer"],
            "effects": ["deal_closed"],
        },
    ],
}

NEGOTIATION_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a negotiation or adversarial "
    "hidden-state scenario, produce a NegotiationSpec JSON wrapped in delimiters.\n\n"
    f"{NEGOTIATION_SPEC_START}\n{{ ... }}\n{NEGOTIATION_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "scenario summary",\n'
    '  "environment_description": "negotiation context",\n'
    '  "initial_state_description": "starting positions",\n'
    '  "hidden_preferences": {\n'
    '    "priorities": {"dimension": weight},\n'
    '    "reservation_value": float,\n'
    '    "aspiration_value": float,\n'
    '    "batna_description": "string"\n'
    "  },\n"
    '  "max_rounds": 5,\n'
    '  "success_criteria": ["criterion"],\n'
    '  "failure_modes": ["failure mode"],\n'
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
    "- hidden_preferences must include priorities, reservation_value, aspiration_value, batna_description\n"
    "- include at least two actions (e.g. make_offer + accept)\n"
    "- max_rounds should be between 2 and 10\n\n"
    f"Example:\n{NEGOTIATION_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{NEGOTIATION_SPEC_END}\n"
)


def parse_negotiation_spec(text: str) -> NegotiationSpec:
    pattern = (
        re.escape(NEGOTIATION_SPEC_START)
        + r"\s*(.*?)\s*"
        + re.escape(NEGOTIATION_SPEC_END)
    )
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain NEGOTIATION_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return NegotiationSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        hidden_preferences=data["hidden_preferences"],
        max_rounds=data.get("max_rounds", 5),
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
        max_steps=data.get("max_steps", 0),
    )


def design_negotiation(
    description: str, llm_fn: LlmFn
) -> NegotiationSpec:
    return parse_negotiation_spec(
        llm_fn(NEGOTIATION_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
