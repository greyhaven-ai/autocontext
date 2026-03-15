from __future__ import annotations

import re

from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "OperatorLoop"


def generate_operator_loop_class(spec: OperatorLoopSpec, name: str) -> str:
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

from autocontext.scenarios.operator_loop import (
    ClarificationRequest,
    EscalationEvent,
    OperatorLoopInterface,
    OperatorLoopResult,
)
from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationResult,
)


class {class_name}(OperatorLoopInterface):
    name = {name!r}
    _escalation_policy = {spec.escalation_policy!r}

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
            "escalations": [],
            "clarifications": [],
            "necessary_escalation_steps": [],
        }}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        completed = set(state.get("completed_actions", []))
        return [
            s for s in self.describe_environment().available_actions
            if s.name not in completed
        ]

    def validate_action(
        self, state: dict[str, Any], action: Action
    ) -> tuple[bool, str]:
        specs = {{
            s.name: s for s in self.describe_environment().available_actions
        }}
        spec = specs.get(action.name)
        if spec is None:
            return False, f"unknown action: {{action.name}}"
        completed = set(state.get("completed_actions", []))
        for req in spec.preconditions:
            if req not in completed:
                return False, f"precondition not met for {{action.name}}: {{req}}"
        return True, ""

    def execute_action(
        self, state: dict[str, Any], action: Action
    ) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        if not valid:
            next_state["failed_actions"] = [
                *state.get("failed_actions", []), action.name
            ]
            return (
                ActionResult(
                    success=False, output="", state_changes={{}}, error=reason
                ),
                next_state,
            )

        next_state["completed_actions"] = [
            *state.get("completed_actions", []), action.name
        ]
        next_state["step"] = state.get("step", 0) + 1
        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}}",
                state_changes={{
                    "completed_actions": list(next_state["completed_actions"])
                }},
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        return (
            required.issubset(completed)
            or state.get("step", 0) >= {spec.max_steps}
        )

    def get_escalation_log(
        self, state: dict[str, Any]
    ) -> list[EscalationEvent]:
        return [
            EscalationEvent.from_dict(e)
            for e in state.get("escalations", [])
        ]

    def get_clarification_log(
        self, state: dict[str, Any]
    ) -> list[ClarificationRequest]:
        return [
            ClarificationRequest.from_dict(c)
            for c in state.get("clarifications", [])
        ]

    def escalate(
        self, state: dict[str, Any], event: EscalationEvent
    ) -> dict[str, Any]:
        next_state = dict(state)
        next_state["escalations"] = [
            *state.get("escalations", []), event.to_dict()
        ]
        return next_state

    def request_clarification(
        self, state: dict[str, Any], request: ClarificationRequest
    ) -> dict[str, Any]:
        next_state = dict(state)
        next_state["clarifications"] = [
            *state.get("clarifications", []), request.to_dict()
        ]
        return next_state

    def evaluate_judgment(
        self, state: dict[str, Any]
    ) -> OperatorLoopResult:
        escalations = state.get("escalations", [])
        clarifications = state.get("clarifications", [])
        total_actions = len(state.get("completed_actions", []))
        necessary_steps = set(state.get("necessary_escalation_steps", []))

        necessary = sum(
            1 for e in escalations if e.get("was_necessary", False)
        )
        unnecessary = len(escalations) - necessary
        missed = len(necessary_steps - {{
            e.get("step", -1) for e in escalations
        }})

        # Action quality: completed actions without failures
        failed = len(state.get("failed_actions", []))
        action_quality = (
            total_actions / max(total_actions + failed, 1)
        )

        # Escalation judgment: penalize both over and under
        if escalations or necessary_steps:
            over_penalty = unnecessary * 0.15
            under_penalty = missed * 0.2
            judgment = max(0.0, 1.0 - over_penalty - under_penalty)
        else:
            judgment = 1.0

        score = round(action_quality * 0.4 + judgment * 0.6, 4)

        return OperatorLoopResult(
            score=score,
            reasoning=(
                f"{{total_actions}} actions, {{len(escalations)}} escalations "
                f"({{necessary}} necessary, {{unnecessary}} unnecessary), "
                f"{{missed}} missed escalations."
            ),
            dimension_scores={{
                "action_quality": round(action_quality, 4),
                "escalation_judgment": round(judgment, 4),
            }},
            total_actions=total_actions,
            escalations=len(escalations),
            necessary_escalations=necessary,
            unnecessary_escalations=unnecessary,
            missed_escalations=missed,
            clarifications_requested=len(clarifications),
        )

    def evaluate_trace(
        self, trace: ActionTrace, final_state: dict[str, Any]
    ) -> SimulationResult:
        judgment = self.evaluate_judgment(final_state)
        action_success = trace.success_rate
        score = round(judgment.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=judgment.reasoning,
            dimension_scores={{
                "action_quality": judgment.dimension_scores.get(
                    "action_quality", 0.0
                ),
                "escalation_judgment": judgment.dimension_scores.get(
                    "escalation_judgment", 0.0
                ),
                "action_success": round(action_success, 4),
            }},
            workflow_complete=self.is_terminal(final_state),
            actions_taken=len(trace.records),
            actions_successful=sum(
                1 for r in trace.records if r.result.success
            ),
            recovery_attempts=judgment.unnecessary_escalations,
            rollback_quality=judgment.dimension_scores.get(
                "escalation_judgment", 0.0
            ),
        )

    def get_rubric(self) -> str:
        return (
            "Evaluate on action completion quality, "
            "escalation judgment (avoiding both over- and under-escalation), "
            "and appropriate use of clarification requests."
        )

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
