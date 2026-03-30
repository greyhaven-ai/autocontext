from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
from autocontext.scenarios.custom.workflow_spec import (
    WorkflowSpec,
    WorkflowStepSpecModel,
)

WORKFLOW_SPEC_START = "<!-- WORKFLOW_SPEC_START -->"
WORKFLOW_SPEC_END = "<!-- WORKFLOW_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Execute an order-processing workflow with compensation when downstream steps fail.",
    "environment_description": "Mock commerce workflow with payment, inventory, and notification side effects.",
    "initial_state_description": "No order steps have run and no side effects have been produced.",
    "workflow_steps": [
        {
            "name": "charge_payment",
            "description": "Charge the customer payment method.",
            "idempotent": False,
            "reversible": True,
            "compensation": "refund_payment",
        },
        {
            "name": "reserve_inventory",
            "description": "Reserve the purchased inventory.",
            "idempotent": True,
            "reversible": True,
            "compensation": "release_inventory",
        },
        {
            "name": "send_confirmation",
            "description": "Send the confirmation notification.",
            "idempotent": True,
            "reversible": False,
        },
    ],
    "success_criteria": [
        "all required workflow steps complete in the correct order",
        "failed steps trigger compensation for reversible side effects",
    ],
    "failure_modes": ["payment failure", "inventory reservation conflict", "notification sent before rollback"],
    "max_steps": 7,
    "actions": [
        {
            "name": "charge_payment",
            "description": "Charge the payment method.",
            "parameters": {"payment_id": "string"},
            "preconditions": [],
            "effects": ["payment_captured"],
        },
        {
            "name": "reserve_inventory",
            "description": "Reserve inventory for the order.",
            "parameters": {"sku": "string"},
            "preconditions": ["charge_payment"],
            "effects": ["inventory_reserved"],
        },
        {
            "name": "send_confirmation",
            "description": "Send a confirmation notification.",
            "parameters": {"channel": "string"},
            "preconditions": ["reserve_inventory"],
            "effects": ["confirmation_sent"],
        },
    ],
}

WORKFLOW_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for a transactional workflow task, "
    "produce a WorkflowSpec JSON wrapped in delimiters.\n\n"
    f"{WORKFLOW_SPEC_START}\n{{ ... }}\n{WORKFLOW_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "human readable workflow summary",\n'
    '  "environment_description": "what system or business process is modeled",\n'
    '  "initial_state_description": "starting state before steps run",\n'
    '  "workflow_steps": [\n'
    "    {\n"
    '      "name": "snake_case_step",\n'
    '      "description": "what the step does",\n'
    '      "idempotent": true,\n'
    '      "reversible": true,\n'
    '      "compensation": "optional_compensation_step"\n'
    "    }\n"
    "  ],\n"
    '  "success_criteria": ["criterion 1", "criterion 2"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 7,\n'
    '  "actions": [\n'
    "    {\n"
    '      "name": "snake_case_action",\n'
    '      "description": "what the action does",\n'
    '      "parameters": {"param": "type"},\n'
    '      "preconditions": ["prior_action"],\n'
    '      "effects": ["effect"]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- model the task as an explicit ordered workflow with transactional side effects\n"
    "- include at least two workflow steps and one reversible step with compensation when appropriate\n"
    "- keep action names aligned to workflow step names when possible\n"
    "- include failure modes that require retry, rollback, or compensation\n"
    "- make the task about executing the workflow, not writing prose about it\n\n"
    f"Example:\n{WORKFLOW_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{WORKFLOW_SPEC_END}\n"
)


def parse_workflow_spec(text: str) -> WorkflowSpec:
    pattern = re.escape(WORKFLOW_SPEC_START) + r"\s*(.*?)\s*" + re.escape(WORKFLOW_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain WORKFLOW_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return WorkflowSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        workflow_steps=[
            WorkflowStepSpecModel(
                name=raw["name"],
                description=raw["description"],
                idempotent=raw["idempotent"],
                reversible=raw["reversible"],
                compensation=raw.get("compensation"),
            )
            for raw in data["workflow_steps"]
        ],
        success_criteria=data["success_criteria"],
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
        failure_modes=data.get("failure_modes", []),
        max_steps=data.get("max_steps", 10),
    )


def design_workflow(description: str, llm_fn: LlmFn) -> WorkflowSpec:
    return parse_workflow_spec(
        llm_fn(WORKFLOW_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
