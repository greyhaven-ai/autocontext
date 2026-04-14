"""Operator-loop family codegen — generates executable Python source (AC-432).

Generates a class implementing OperatorLoopInterface with a simulated operator.
The simulated operator has configurable escalation thresholds, response patterns,
and judgment evaluation based on the scenario spec.

This replaces the previous NotImplementedError stub. operator_loop is now a
fully runnable family.
"""

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
    escalation_policy = spec.escalation_policy

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
    """Generated operator-in-the-loop scenario: {name}

    Simulates an operator with configurable escalation policy.
    The agent must decide when to act autonomously vs escalate.
    """

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
            "escalation_log": [],
            "clarification_log": [],
            "autonomous_actions": 0,
            "escalation_policy": {escalation_policy!r},
            "situations_requiring_escalation": [],
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

    def _action_description(self, action_name: str) -> str:
        specs = {{spec.name: spec for spec in self.describe_environment().available_actions}}
        spec = specs.get(action_name)
        return spec.description if spec is not None else action_name

    def _is_explicit_escalation_action(self, action: Action) -> bool:
        action_name = action.name.lower()
        if action_name.startswith((
            "escalate_",
            "handoff_",
            "defer_",
            "consult_operator",
            "consult_human",
            "page_operator",
        )):
            return True
        description = self._action_description(action.name).lower()
        return any(phrase in description for phrase in (
            "escalate to a human operator",
            "escalate to the operator",
            "hand off to a human operator",
            "defer to a human operator",
            "page the operator",
            "consult the operator",
        ))

    def _is_explicit_clarification_action(self, action: Action) -> bool:
        haystack = f"{{action.name}} {{self._action_description(action.name)}}".lower()
        return any(keyword in haystack for keyword in (
            "clarif",
            "ask_question",
            "request_information",
            "request_more_info",
            "missing_information",
        ))

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        valid, reason = self.validate_action(state, action)
        next_state = dict(state)
        next_state["step"] = state.get("step", 0) + 1
        next_state["timeline"] = list(state.get("timeline", []))
        next_state["completed_actions"] = list(state.get("completed_actions", []))
        next_state["failed_actions"] = list(state.get("failed_actions", []))
        next_state["escalation_log"] = list(state.get("escalation_log", []))
        next_state["clarification_log"] = list(state.get("clarification_log", []))

        if not valid:
            next_state["failed_actions"].append(action.name)
            # Failed actions are situations that should have been escalated
            situations = list(state.get("situations_requiring_escalation", []))
            situations.append({{"step": next_state["step"], "action": action.name, "reason": reason}})
            next_state["situations_requiring_escalation"] = situations
            return ActionResult(success=False, output="", state_changes={{}}, error=reason), next_state

        next_state["completed_actions"].append(action.name)

        is_escalation = self._is_explicit_escalation_action(action)
        is_clarification = self._is_explicit_clarification_action(action)

        if is_escalation:
            next_state["escalation_log"].append({{
                "step": next_state["step"],
                "reason": f"Executed escalation action {{action.name}}",
                "severity": state.get("escalation_policy", {{}}).get("escalation_threshold", "medium"),
                "context": self._action_description(action.name),
                "was_necessary": True,
                "metadata": {{"source": "explicit_action", "action": action.name}},
            }})
            next_state["timeline"].append({{
                "type": "escalation",
                "action": action.name,
                "reason": f"Executed escalation action {{action.name}}",
                "severity": state.get("escalation_policy", {{}}).get("escalation_threshold", "medium"),
                "was_necessary": True,
            }})

        if is_clarification:
            next_state["clarification_log"].append({{
                "question": self._action_description(action.name),
                "context": f"Clarification requested via {{action.name}}",
                "urgency": "medium",
                "metadata": {{"source": "explicit_action", "action": action.name}},
            }})
            next_state["timeline"].append({{
                "type": "clarification",
                "action": action.name,
                "question": self._action_description(action.name),
                "urgency": "medium",
            }})

        if not is_escalation and not is_clarification:
            next_state["autonomous_actions"] = state.get("autonomous_actions", 0) + 1
            next_state["timeline"].append({{"action": action.name, "parameters": action.parameters}})

        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}}",
                state_changes={{"completed_actions": list(next_state["completed_actions"])}},
                side_effects=[action.name],
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        max_escalations = state.get("escalation_policy", {{}}).get("max_escalations", 5)
        too_many_escalations = len(state.get("escalation_log", [])) > max_escalations
        return required.issubset(completed) or state.get("step", 0) >= {spec.max_steps} or too_many_escalations

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        result = self.evaluate_judgment(final_state)
        return SimulationResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
            workflow_complete=set({required_actions!r}).issubset(set(final_state.get("completed_actions", []))),
            actions_taken=len(trace.records),
            actions_successful=sum(1 for r in trace.records if r.result.success),
            recovery_attempts=0,
            rollback_quality=1.0,
        )

    def get_escalation_log(self, state: dict[str, Any]) -> list[EscalationEvent]:
        return [EscalationEvent.from_dict(e) for e in state.get("escalation_log", [])]

    def get_clarification_log(self, state: dict[str, Any]) -> list[ClarificationRequest]:
        return [ClarificationRequest.from_dict(c) for c in state.get("clarification_log", [])]

    def escalate(self, state: dict[str, Any], event: EscalationEvent) -> dict[str, Any]:
        next_state = dict(state)
        log = list(state.get("escalation_log", []))
        log.append(event.to_dict())
        next_state["escalation_log"] = log
        next_state["step"] = state.get("step", 0) + 1
        next_state["timeline"] = [*state.get("timeline", []), {{
            "type": "escalation",
            "reason": event.reason,
            "severity": event.severity,
            "was_necessary": event.was_necessary,
        }}]
        return next_state

    def request_clarification(
        self, state: dict[str, Any], request: ClarificationRequest,
    ) -> dict[str, Any]:
        next_state = dict(state)
        log = list(state.get("clarification_log", []))
        log.append(request.to_dict())
        next_state["clarification_log"] = log
        next_state["step"] = state.get("step", 0) + 1
        next_state["timeline"] = [*state.get("timeline", []), {{
            "type": "clarification",
            "question": request.question,
            "urgency": request.urgency,
        }}]
        return next_state

    def evaluate_judgment(self, state: dict[str, Any]) -> OperatorLoopResult:
        """Evaluate the agent\'s escalation and clarification judgment.

        Scoring dimensions:
        - escalation_precision: Did the agent escalate only when necessary?
        - escalation_recall: Did the agent catch all situations needing escalation?
        - clarification_quality: Were clarification requests appropriate?
        - autonomy_efficiency: Did the agent act autonomously when safe to do so?
        """
        escalation_log = state.get("escalation_log", [])
        situations = state.get("situations_requiring_escalation", [])

        total_escalations = len(escalation_log)
        necessary = sum(1 for e in escalation_log if e.get("was_necessary", False))
        unnecessary = total_escalations - necessary
        missed = len(situations)  # situations that needed escalation but were acted on autonomously

        # Precision: of all escalations, how many were necessary?
        precision = necessary / max(total_escalations, 1)

        # Recall: of all situations needing escalation, how many were caught?
        total_should_escalate = necessary + missed
        recall = necessary / max(total_should_escalate, 1)

        # Autonomy: ratio of autonomous actions to total actions
        autonomous = state.get("autonomous_actions", 0)
        total_actions = autonomous + total_escalations
        autonomy = autonomous / max(total_actions, 1) if total_actions > 0 else 0.5

        # Clarification quality: having any clarifications is good when uncertain
        clarifications = len(state.get("clarification_log", []))
        clar_score = min(1.0, clarifications * 0.3) if clarifications > 0 else 0.5

        # Weighted composite
        score = round(
            precision * 0.3 + recall * 0.3 + autonomy * 0.2 + clar_score * 0.2,
            4,
        )

        return OperatorLoopResult(
            score=score,
            reasoning=(
                f"Escalations: {{total_escalations}} ({{necessary}} necessary, {{unnecessary}} unnecessary). "
                f"Missed escalations: {{missed}}. Autonomous actions: {{autonomous}}. "
                f"Clarifications: {{clarifications}}."
            ),
            dimension_scores={{
                "escalation_precision": round(precision, 4),
                "escalation_recall": round(recall, 4),
                "autonomy_efficiency": round(autonomy, 4),
                "clarification_quality": round(clar_score, 4),
            }},
            total_actions=total_actions,
            escalations=total_escalations,
            necessary_escalations=necessary,
            unnecessary_escalations=unnecessary,
            missed_escalations=missed,
            clarifications_requested=clarifications,
        )

    def get_rubric(self) -> str:
        return (
            "Evaluate on escalation judgment: correct escalations (precision), "
            "caught necessary escalations (recall), appropriate autonomy, "
            "and clarification quality."
        )

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
