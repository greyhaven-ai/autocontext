from __future__ import annotations

import re

from autocontext.scenarios.custom.negotiation_spec import NegotiationSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "Negotiation"


def generate_negotiation_class(spec: NegotiationSpec, name: str) -> str:
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

from autocontext.scenarios.negotiation import (
    HiddenPreferences,
    NegotiationInterface,
    NegotiationResult,
    NegotiationRound,
    OpponentModel,
)
from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationResult,
)


class {class_name}(NegotiationInterface):
    name = {name!r}
    _hidden_prefs_spec = {spec.hidden_preferences!r}

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
            "round": 0,
            "max_rounds": {spec.max_rounds},
            "rounds": [],
            "completed_actions": [],
            "failed_actions": [],
            "opponent_model": None,
            "deal_value": None,
            "deal_closed": False,
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
        next_state["round"] = state.get("round", 0) + 1

        # Record round
        offer = action.parameters if action.parameters else {{}}
        rnd = {{
            "round_number": next_state["round"],
            "offer": offer,
            "counter_offer": None,
            "accepted": action.name == "accept",
            "agent_reasoning": action.parameters.get("reasoning", "")
            if action.parameters else "",
        }}
        next_state["rounds"] = [*state.get("rounds", []), rnd]

        if action.name == "accept":
            next_state["deal_closed"] = True
            # Compute simple deal value from round count
            max_r = state.get("max_rounds", {spec.max_rounds})
            rounds_used = next_state["round"]
            prefs = self._hidden_prefs_spec
            reservation = prefs.get("reservation_value", 0.0)
            aspiration = prefs.get("aspiration_value", 100.0)
            # More rounds used → closer to reservation; fewer → closer to aspiration
            ratio = 1.0 - (rounds_used / max(max_r, 1))
            next_state["deal_value"] = round(
                reservation + ratio * (aspiration - reservation), 2
            )

        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}} (round {{next_state['round']}})",
                state_changes={{"round": next_state["round"]}},
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        return (
            state.get("deal_closed", False)
            or required.issubset(completed)
            or state.get("round", 0) >= state.get("max_rounds", {spec.max_rounds})
            or state.get("step", 0) >= {spec.max_steps}
        )

    def get_hidden_preferences(
        self, state: dict[str, Any]
    ) -> HiddenPreferences:
        return HiddenPreferences(
            priorities=self._hidden_prefs_spec.get("priorities", {{}}),
            reservation_value=self._hidden_prefs_spec.get(
                "reservation_value", 0.0
            ),
            aspiration_value=self._hidden_prefs_spec.get(
                "aspiration_value", 100.0
            ),
            batna_description=self._hidden_prefs_spec.get(
                "batna_description", ""
            ),
        )

    def get_rounds(self, state: dict[str, Any]) -> list[NegotiationRound]:
        return [NegotiationRound.from_dict(r) for r in state.get("rounds", [])]

    def get_opponent_model(
        self, state: dict[str, Any]
    ) -> OpponentModel | None:
        om = state.get("opponent_model")
        if om is None:
            return None
        return OpponentModel.from_dict(om)

    def update_opponent_model(
        self, state: dict[str, Any], model: OpponentModel
    ) -> dict[str, Any]:
        next_state = dict(state)
        next_state["opponent_model"] = model.to_dict()
        return next_state

    def evaluate_negotiation(
        self, state: dict[str, Any]
    ) -> NegotiationResult:
        prefs = self.get_hidden_preferences(state)
        deal_value = state.get("deal_value") or 0.0
        rounds_used = state.get("round", 0)
        max_rounds = state.get("max_rounds", {spec.max_rounds})

        # Deal quality: how much above reservation?
        surplus = prefs.aspiration_value - prefs.reservation_value
        if surplus > 0:
            value_ratio = max(
                0.0,
                (deal_value - prefs.reservation_value) / surplus,
            )
        else:
            value_ratio = 1.0 if deal_value >= prefs.reservation_value else 0.0

        # Efficiency: fewer rounds → higher score
        efficiency = 1.0 - (rounds_used / max(max_rounds, 1))

        # Opponent modeling accuracy
        om = self.get_opponent_model(state)
        if om is not None:
            # Compare inferred priorities to actual
            actual = prefs.priorities
            diffs = []
            for dim, actual_w in actual.items():
                inferred_w = om.inferred_priorities.get(dim, 0.0)
                diffs.append(abs(actual_w - inferred_w))
            model_accuracy = max(0.0, 1.0 - (sum(diffs) / max(len(diffs), 1)))
        else:
            model_accuracy = 0.0

        # Adaptation: did the agent update its model?
        adaptation = min(1.0, len(state.get("rounds", [])) * 0.2)

        score = round(
            value_ratio * 0.35
            + model_accuracy * 0.25
            + efficiency * 0.2
            + adaptation * 0.2,
            4,
        )

        return NegotiationResult(
            score=score,
            reasoning=(
                f"Deal value {{deal_value}} "
                f"({{rounds_used}}/{{max_rounds}} rounds). "
                f"Model accuracy: {{model_accuracy:.2f}}."
            ),
            dimension_scores={{
                "deal_quality": round(value_ratio, 4),
                "opponent_modeling": round(model_accuracy, 4),
                "efficiency": round(efficiency, 4),
                "adaptation": round(adaptation, 4),
            }},
            deal_value=deal_value,
            rounds_used=rounds_used,
            max_rounds=max_rounds,
            opponent_model_accuracy=round(model_accuracy, 4),
            value_claimed_ratio=round(value_ratio, 4),
        )

    def evaluate_trace(
        self, trace: ActionTrace, final_state: dict[str, Any]
    ) -> SimulationResult:
        neg_result = self.evaluate_negotiation(final_state)
        action_success = trace.success_rate
        score = round(neg_result.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=neg_result.reasoning,
            dimension_scores={{
                "deal_quality": neg_result.dimension_scores.get(
                    "deal_quality", 0.0
                ),
                "opponent_modeling": neg_result.dimension_scores.get(
                    "opponent_modeling", 0.0
                ),
                "efficiency": neg_result.dimension_scores.get(
                    "efficiency", 0.0
                ),
                "adaptation": neg_result.dimension_scores.get(
                    "adaptation", 0.0
                ),
                "action_success": round(action_success, 4),
            }},
            workflow_complete=final_state.get("deal_closed", False),
            actions_taken=len(trace.records),
            actions_successful=sum(
                1 for r in trace.records if r.result.success
            ),
            recovery_attempts=len(final_state.get("failed_actions", [])),
            rollback_quality=neg_result.dimension_scores.get(
                "efficiency", 0.0
            ),
        )

    def get_rubric(self) -> str:
        return (
            "Evaluate on deal quality relative to BATNA, "
            "opponent modeling accuracy, negotiation efficiency, "
            "and strategic adaptation across rounds."
        )

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
