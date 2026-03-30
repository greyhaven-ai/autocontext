from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.investigation_spec import InvestigationSpec
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

INVESTIGATION_SPEC_START = "<!-- INVESTIGATION_SPEC_START -->"
INVESTIGATION_SPEC_END = "<!-- INVESTIGATION_SPEC_END -->"

_EXAMPLE_SPEC = {
    "description": "Investigate a production outage by gathering evidence and identifying the root cause.",
    "environment_description": "Mock service environment with logs, dashboards, and deployment metadata.",
    "initial_state_description": "An API outage has started and only partial evidence is visible.",
    "evidence_pool_description": (
        "Service logs implicate the auth service, dashboard metrics show latency spikes, "
        "and an unrelated cron job log is a red herring."
    ),
    "diagnosis_target": "A bad auth-service deployment exhausted the database connection pool.",
    "success_criteria": [
        "collect enough evidence to explain the outage",
        "identify the correct diagnosis without relying on red herrings",
    ],
    "failure_modes": ["following a cron-job red herring", "stopping before enough evidence is collected"],
    "max_steps": 6,
    "actions": [
        {
            "name": "inspect_logs",
            "description": "Review service logs around the incident window.",
            "parameters": {"service": "string"},
            "preconditions": [],
            "effects": ["log_evidence_collected"],
        },
        {
            "name": "query_metrics",
            "description": "Check dashboard metrics related to the outage.",
            "parameters": {"metric": "string"},
            "preconditions": [],
            "effects": ["metrics_evidence_collected"],
        },
        {
            "name": "record_diagnosis",
            "description": "Submit the final diagnosis grounded in collected evidence.",
            "parameters": {"diagnosis": "string"},
            "preconditions": ["inspect_logs", "query_metrics"],
            "effects": ["diagnosis_recorded"],
        },
    ],
}

INVESTIGATION_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for an investigation or debugging task, "
    "produce an InvestigationSpec JSON wrapped in delimiters.\n\n"
    f"{INVESTIGATION_SPEC_START}\n{{ ... }}\n{INVESTIGATION_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "description": "human readable investigation summary",\n'
    '  "environment_description": "what environment or system is being investigated",\n'
    '  "initial_state_description": "starting state and visible symptoms",\n'
    '  "evidence_pool_description": "what evidence exists, including any red herrings",\n'
    '  "diagnosis_target": "the correct root cause or diagnosis",\n'
    '  "success_criteria": ["criterion 1", "criterion 2"],\n'
    '  "failure_modes": ["failure mode"],\n'
    '  "max_steps": 6,\n'
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
    "- model the task around gathering evidence and reaching a diagnosis, not writing an essay about debugging\n"
    "- include one explicit diagnosis target and mention at least one red herring in the evidence pool description\n"
    "- make action names short and snake_case\n"
    "- include at least two success criteria and at least two actions\n"
    "- reserve one action for recording or submitting the diagnosis\n\n"
    f"Example:\n{INVESTIGATION_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{INVESTIGATION_SPEC_END}\n"
)


def parse_investigation_spec(text: str) -> InvestigationSpec:
    pattern = re.escape(INVESTIGATION_SPEC_START) + r"\s*(.*?)\s*" + re.escape(INVESTIGATION_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain INVESTIGATION_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return InvestigationSpec(
        description=data["description"],
        environment_description=data["environment_description"],
        initial_state_description=data["initial_state_description"],
        evidence_pool_description=data["evidence_pool_description"],
        diagnosis_target=data["diagnosis_target"],
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


def design_investigation(description: str, llm_fn: LlmFn) -> InvestigationSpec:
    return parse_investigation_spec(
        llm_fn(INVESTIGATION_DESIGNER_SYSTEM, f"User description:\n{description}")
    )
