from __future__ import annotations

import re

from autocontext.scenarios.custom.tool_fragility_spec import ToolFragilitySpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "ToolFragility"


def generate_tool_fragility_class(spec: ToolFragilitySpec, name: str) -> str:
    class_name = _class_name(name)
    action_specs = ",\n".join(
        "            ActionSpec("
        f"name={action.name!r}, "
        f"description={action.description!r}, "
        f"parameters={action.parameters!r}, "
        f"preconditions={action.preconditions!r}, "
        f"effects={action.effects!r})"
        for action in spec.actions
    )
    tool_contracts_repr = [
        {
            "tool_name": tc.tool_name,
            "version": tc.version,
            "description": tc.description,
        }
        for tc in spec.tool_contracts
    ]
    required_actions = [action.name for action in spec.actions]
    return f'''from __future__ import annotations

from typing import Any

from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationResult,
)
from autocontext.scenarios.tool_fragility import (
    FailureAttribution,
    ToolContract,
    ToolDrift,
    ToolFragilityInterface,
    ToolFragilityResult,
)


class {class_name}(ToolFragilityInterface):
    name = {name!r}
    _tool_contracts_spec = {tool_contracts_repr!r}

    def describe_scenario(self) -> str:
        return {spec.description!r}

    def describe_environment(self) -> EnvironmentSpec:
        return EnvironmentSpec(
            name={name!r},
            description={spec.environment_description!r},
            available_actions=[
{action_specs}
            ],
            initial_state_description={spec.initial_state_description!r},
            success_criteria={spec.success_criteria!r},
            failure_modes={spec.failure_modes!r},
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {{
            "seed": seed or 0,
            "step": 0,
            "tool_versions": {{tc["tool_name"]: tc["version"] for tc in self._tool_contracts_spec}},
            "drifts_applied": [],
            "completed_actions": [],
            "failed_actions": [],
            "drifts_detected": 0,
            "drifts_adapted": 0,
            "wasted_attempts": 0,
            "failure_attributions": [],
        }}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        completed = set(state.get("completed_actions", []))
        return [s for s in self.describe_environment().available_actions if s.name not in completed]

    def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
        specs = {{s.name: s for s in self.describe_environment().available_actions}}
        spec = specs.get(action.name)
        if spec is None:
            return False, f"unknown action: {{action.name}}"
        completed = set(state.get("completed_actions", []))
        for req in spec.preconditions:
            if req not in completed:
                return False, f"precondition not met for {{action.name}}: {{req}}"
        return True, ""

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            next_state["wasted_attempts"] = state.get("wasted_attempts", 0) + 1
            return ActionResult(success=False, output="", state_changes={{}}, error=reason), next_state

        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}}",
                state_changes={{"completed_actions": list(next_state["completed_actions"])}},
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("step", 0) >= {spec.max_steps}

    def get_tool_contracts(self, state: dict[str, Any]) -> list[ToolContract]:
        versions = state.get("tool_versions", {{}})
        return [
            ToolContract(
                tool_name=tc["tool_name"],
                version=versions.get(tc["tool_name"], tc["version"]),
                input_schema={{}},
                output_schema={{}},
                description=tc["description"],
            )
            for tc in self._tool_contracts_spec
        ]

    def get_drift_log(self, state: dict[str, Any]) -> list[ToolDrift]:
        return [ToolDrift.from_dict(d) for d in state.get("drifts_applied", [])]

    def inject_drift(self, state: dict[str, Any], drift: ToolDrift) -> dict[str, Any]:
        next_state = dict(state)
        next_state["drifts_applied"] = [*state.get("drifts_applied", []), drift.to_dict()]
        tv = dict(state.get("tool_versions", {{}}))
        tv[drift.tool_name] = drift.to_version
        next_state["tool_versions"] = tv
        return next_state

    def attribute_failure(
        self, state: dict[str, Any], step: int, error: str
    ) -> FailureAttribution:
        drifts = state.get("drifts_applied", [])
        if drifts:
            return FailureAttribution(
                step=step, failure_class="tool_failure",
                description=error, tool_name=drifts[-1].get("tool_name", "unknown"),
                recoverable=True,
            )
        return FailureAttribution(
            step=step, failure_class="routing_failure",
            description=error, tool_name="unknown",
            recoverable=True,
        )

    def evaluate_fragility(self, state: dict[str, Any]) -> ToolFragilityResult:
        drifts_injected = len(state.get("drifts_applied", []))
        detected = state.get("drifts_detected", 0)
        adapted = state.get("drifts_adapted", 0)
        wasted = state.get("wasted_attempts", 0)
        adaptation_rate = adapted / max(drifts_injected, 1)
        waste_penalty = min(wasted * 0.1, 0.5)
        score = round(max(0.0, adaptation_rate - waste_penalty), 4)
        return ToolFragilityResult(
            score=score,
            reasoning=f"Adapted to {{adapted}}/{{drifts_injected}} drifts with {{wasted}} wasted attempts.",
            dimension_scores={{"adaptation": round(adaptation_rate, 4), "waste_avoidance": round(1.0 - waste_penalty, 4)}},
            drifts_injected=drifts_injected,
            drifts_detected=detected,
            drifts_adapted=adapted,
            wasted_attempts=wasted,
            failure_attributions=[
                FailureAttribution.from_dict(fa) for fa in state.get("failure_attributions", [])
            ],
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        fragility = self.evaluate_fragility(final_state)
        action_success = trace.success_rate
        score = round(fragility.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=fragility.reasoning,
            dimension_scores={{
                "adaptation": fragility.dimension_scores.get("adaptation", 0.0),
                "waste_avoidance": fragility.dimension_scores.get("waste_avoidance", 0.0),
                "action_success": round(action_success, 4),
            }},
            workflow_complete=fragility.drifts_adapted == fragility.drifts_injected,
            actions_taken=len(trace.records),
            actions_successful=sum(1 for r in trace.records if r.result.success),
            recovery_attempts=fragility.wasted_attempts,
            rollback_quality=fragility.dimension_scores.get("waste_avoidance", 0.0),
        )

    def get_rubric(self) -> str:
        return "Evaluate on drift detection, tool adaptation quality, and wasted attempt minimization."

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
