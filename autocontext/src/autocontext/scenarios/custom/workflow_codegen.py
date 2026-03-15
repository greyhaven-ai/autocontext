from __future__ import annotations

import re

from autocontext.scenarios.custom.workflow_spec import WorkflowSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "Workflow"


def generate_workflow_class(spec: WorkflowSpec, name: str) -> str:
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
    workflow_steps = [
        {
            "name": step.name,
            "description": step.description,
            "idempotent": step.idempotent,
            "reversible": step.reversible,
            "compensation": step.compensation,
        }
        for step in spec.workflow_steps
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
from autocontext.scenarios.workflow import (
    CompensationAction,
    SideEffect,
    WorkflowInterface,
    WorkflowResult,
    WorkflowStep,
)


class {class_name}(WorkflowInterface):
    name = {name!r}
    _workflow_step_defs = {workflow_steps!r}

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
            "completed_steps": [],
            "side_effects": [],
            "compensations": [],
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

    def get_workflow_steps(self) -> list[WorkflowStep]:
        return [
            WorkflowStep(
                name=raw["name"],
                description=raw["description"],
                idempotent=raw["idempotent"],
                reversible=raw["reversible"],
                compensation=raw.get("compensation"),
            )
            for raw in self._workflow_step_defs
        ]

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["timeline"] = list(state.get("timeline", []))
        next_state["side_effects"] = [dict(effect) for effect in state.get("side_effects", [])]
        next_state["compensations"] = [dict(comp) for comp in state.get("compensations", [])]
        if not valid:
            next_state["failed_actions"] = [*state.get("failed_actions", []), action.name]
            return ActionResult(success=False, output="", state_changes={{}}, error=reason), next_state

        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]
        next_state["completed_steps"] = [*state.get("completed_steps", []), action.name]
        next_state["timeline"].append({{"action": action.name, "parameters": action.parameters}})
        workflow_steps = {{step.name: step for step in self.get_workflow_steps()}}
        step = workflow_steps.get(action.name)
        if step is not None:
            next_state["side_effects"].append(
                {{
                    "step_name": step.name,
                    "effect_type": "workflow_step",
                    "description": step.description,
                    "reversible": step.reversible,
                    "reversed": False,
                }}
            )
        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}}",
                state_changes={{
                    "completed_actions": list(next_state["completed_actions"]),
                    "completed_steps": list(next_state["completed_steps"]),
                }},
                side_effects=[action.name],
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        return required.issubset(completed) or state.get("step", 0) >= {spec.max_steps}

    def execute_step(self, state: dict[str, Any], step: WorkflowStep) -> tuple[ActionResult, dict[str, Any]]:
        return self.execute_action(state, Action(name=step.name, parameters={{}}))

    def execute_compensation(self, state: dict[str, Any], step: WorkflowStep) -> CompensationAction:
        side_effects = [dict(effect) for effect in state.get("side_effects", [])]
        success = False
        for effect in side_effects:
            if effect["step_name"] == step.name and effect["reversible"] and not effect["reversed"]:
                effect["reversed"] = True
                success = True
        state["side_effects"] = side_effects
        state.setdefault("compensations", []).append(
            {{
                "step_name": step.name,
                "compensation_name": step.compensation or f"undo_{{step.name}}",
                "success": success,
                "output": "Compensation executed" if success else "No reversible side effect found",
            }}
        )
        return CompensationAction(
            step_name=step.name,
            compensation_name=step.compensation or f"undo_{{step.name}}",
            success=success,
            output="Compensation executed" if success else "No reversible side effect found",
        )

    def get_side_effects(self, state: dict[str, Any]) -> list[SideEffect]:
        return [
            SideEffect(
                step_name=effect["step_name"],
                effect_type=effect["effect_type"],
                description=effect["description"],
                reversible=effect["reversible"],
                reversed=effect["reversed"],
            )
            for effect in state.get("side_effects", [])
        ]

    def evaluate_workflow(self, state: dict[str, Any]) -> WorkflowResult:
        steps = self.get_workflow_steps()
        side_effects = self.get_side_effects(state)
        reversed_count = sum(1 for effect in side_effects if effect.reversed)
        leaked_count = sum(1 for effect in side_effects if effect.reversible and not effect.reversed)
        compensations = state.get("compensations", [])
        completion = len(state.get("completed_steps", [])) / len(steps) if steps else 1.0
        compensation_quality = (
            sum(1 for comp in compensations if comp.get("success")) / max(len(compensations), 1)
            if compensations else (1.0 if leaked_count == 0 else 0.0)
        )
        containment = 1.0 if leaked_count == 0 else max(0.0, 1.0 - (leaked_count / max(len(side_effects), 1)))
        score = round((completion * 0.5) + (compensation_quality * 0.3) + (containment * 0.2), 4)
        return WorkflowResult(
            score=score,
            reasoning=f"Completed {{len(state.get('completed_steps', []))}} of {{len(steps)}} workflow steps.",
            dimension_scores={{
                "completeness": round(completion, 4),
                "compensation_quality": round(compensation_quality, 4),
                "side_effect_containment": round(containment, 4),
            }},
            steps_completed=len(state.get("completed_steps", [])),
            steps_total=len(steps),
            retries=sum(1 for action_name in state.get("failed_actions", []) if action_name in {{step.name for step in steps}}),
            compensations_triggered=len(compensations),
            compensations_successful=sum(1 for comp in compensations if comp.get("success")),
            side_effects=side_effects,
            side_effects_reversed=reversed_count,
            side_effects_leaked=leaked_count,
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        workflow = self.evaluate_workflow(final_state)
        return SimulationResult(
            score=workflow.score,
            reasoning=workflow.reasoning,
            dimension_scores={{
                "completeness": workflow.dimension_scores["completeness"],
                "compensation_quality": workflow.dimension_scores["compensation_quality"],
                "side_effect_containment": workflow.dimension_scores["side_effect_containment"],
            }},
            workflow_complete=workflow.steps_completed == workflow.steps_total,
            actions_taken=len(trace.records),
            actions_successful=sum(1 for record in trace.records if record.result.success),
            recovery_attempts=workflow.retries,
            rollback_quality=workflow.dimension_scores["compensation_quality"],
        )

    def get_rubric(self) -> str:
        return "Evaluate on workflow completeness, compensation quality, and side-effect containment."

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
