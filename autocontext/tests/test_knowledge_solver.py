from __future__ import annotations

import json
from pathlib import Path

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.custom.operator_loop_designer import OPERATOR_LOOP_SPEC_END, OPERATOR_LOOP_SPEC_START
from autocontext.scenarios.families import detect_family


def _operator_loop_llm(system: str, user: str) -> str:
    del system, user
    spec = {
        "description": "A support queue where high-risk actions require operator escalation.",
        "environment_description": "The agent triages support requests and can defer risky actions to a human operator.",
        "initial_state_description": "A new request is waiting for triage.",
        "escalation_policy": {"escalation_threshold": "high", "max_escalations": 2},
        "success_criteria": [
            "routine issues are handled safely",
            "high-risk actions are escalated to a human operator",
        ],
        "failure_modes": ["unsafe autonomous handling"],
        "max_steps": 5,
        "actions": [
            {
                "name": "review_request",
                "description": "Assess the request and available evidence.",
                "parameters": {},
                "preconditions": [],
                "effects": ["request_reviewed"],
            },
            {
                "name": "escalate_to_human_operator",
                "description": "Escalate the risky request to a human operator.",
                "parameters": {},
                "preconditions": ["review_request"],
                "effects": ["operator_guidance_available"],
            },
            {
                "name": "continue_with_operator_guidance",
                "description": "Resume handling after operator guidance is received.",
                "parameters": {},
                "preconditions": ["escalate_to_human_operator"],
                "effects": ["request_resolved"],
            },
        ],
    }
    return f"{OPERATOR_LOOP_SPEC_START}\n{json.dumps(spec)}\n{OPERATOR_LOOP_SPEC_END}"


class TestSolveScenarioBuilder:
    def test_routes_operator_loop_descriptions_to_operator_loop_creator(self, tmp_path: Path) -> None:
        from autocontext.knowledge.solver import SolveScenarioBuilder

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=_operator_loop_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )

        result = builder.build(
            "Create and solve an operator-loop escalation scenario for an autonomous support agent "
            "that escalates high-risk account actions to a human operator."
        )

        scenario_dir = tmp_path / "_custom_scenarios" / result.scenario_name
        spec_payload = json.loads((scenario_dir / "spec.json").read_text(encoding="utf-8"))
        scenario = SCENARIO_REGISTRY[result.scenario_name]()

        assert result.family_name == "operator_loop"
        assert spec_payload["scenario_type"] == "operator_loop"
        assert detect_family(scenario).name == "operator_loop"

    def test_keeps_legacy_game_creator_for_game_descriptions(self, tmp_path: Path) -> None:
        from autocontext.knowledge.solver import SolveScenarioBuilder

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=_operator_loop_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )

        result = builder.build("Create and solve a resource management game about balancing mining and defense.")

        scenario_dir = tmp_path / "_custom_scenarios" / result.scenario_name
        spec_payload = json.loads((scenario_dir / "spec.json").read_text(encoding="utf-8"))
        scenario = SCENARIO_REGISTRY[result.scenario_name]()

        assert result.family_name == "game"
        assert spec_payload["scenario_type"] == "parametric"
        assert detect_family(scenario).name == "game"
