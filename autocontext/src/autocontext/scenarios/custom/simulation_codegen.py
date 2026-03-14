from __future__ import annotations

import re

from autocontext.scenarios.custom.simulation_spec import SimulationSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "Simulation"


def generate_simulation_class(spec: SimulationSpec, name: str) -> str:
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
    required_actions = [action.name for action in spec.actions]
    return f'''from __future__ import annotations

from typing import Any

from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationInterface,
    SimulationResult,
)


class {class_name}(SimulationInterface):
    name = {name!r}

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
            "completed_actions": [],
            "failed_actions": [],
            "timeline": [],
            "terminal": False,
        }}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        completed = set(state.get("completed_actions", []))
        return [spec for spec in self.describe_environment().available_actions if spec.name not in completed]

    def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
        specs = {{spec.name: spec for spec in self.describe_environment().available_actions}}
        spec = specs.get(action.name)
        if spec is None:
            return False, f"unknown action: {{action.name}}"
        completed = set(state.get("completed_actions", []))
        for requirement in spec.preconditions:
            if requirement not in completed:
                return False, f"precondition not met for {{action.name}}: {{requirement}}"
        return True, ""

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["timeline"] = list(state.get("timeline", []))
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={{}}, error=reason), next_state
        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["timeline"].append({{"action": action.name, "parameters": action.parameters}})
        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}}",
                state_changes={{
                    "completed_actions": list(next_state["completed_actions"])
                }},
                side_effects=[action.name],
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("step", 0) >= {spec.max_steps}

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        required = set({required_actions!r})
        completed = set(final_state.get("completed_actions", []))
        completion = len(required & completed) / len(required) if required else 1.0
        ordering = trace.success_rate
        failures = sum(1 for record in trace.records if not record.result.success)
        recovery = 1.0 if failures == 0 else max(0.2, 1.0 - (failures / max(len(trace.records), 1)))
        score = round((completion * 0.5) + (ordering * 0.3) + (recovery * 0.2), 4)
        return SimulationResult(
            score=score,
            reasoning=f"Completed {{len(completed)}} of {{len(required)}} required actions.",
            dimension_scores={{
                "completion": round(completion, 4),
                "ordering": round(ordering, 4),
                "recovery": round(recovery, 4),
            }},
            workflow_complete=required.issubset(completed),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=failures,
            rollback_quality=1.0 if failures == 0 else recovery,
        )

    def get_rubric(self) -> str:
        return "Evaluate on completion, correct dependency ordering, and recovery quality."

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
