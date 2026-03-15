from __future__ import annotations

import re

from autocontext.scenarios.custom.coordination_spec import CoordinationSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "Coordination"


def generate_coordination_class(spec: CoordinationSpec, name: str) -> str:
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

from autocontext.scenarios.coordination import (
    CoordinationInterface,
    CoordinationResult,
    HandoffRecord,
    WorkerContext,
)
from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationResult,
)


class {class_name}(CoordinationInterface):
    name = {name!r}
    _workers_spec = {spec.workers!r}

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
            "handoffs": [],
            "worker_outputs": {{}},
            "merged": False,
            "merge_conflicts": 0,
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
            or state.get("merged", False)
            or state.get("step", 0) >= {spec.max_steps}
        )

    def get_worker_contexts(
        self, state: dict[str, Any]
    ) -> list[WorkerContext]:
        return [
            WorkerContext(
                worker_id=w["worker_id"],
                role=w.get("role", "worker"),
                context_partition={{}},
                visible_data=[],
            )
            for w in self._workers_spec
        ]

    def get_handoff_log(
        self, state: dict[str, Any]
    ) -> list[HandoffRecord]:
        return [
            HandoffRecord.from_dict(h) for h in state.get("handoffs", [])
        ]

    def record_handoff(
        self, state: dict[str, Any], handoff: HandoffRecord
    ) -> dict[str, Any]:
        next_state = dict(state)
        next_state["handoffs"] = [
            *state.get("handoffs", []), handoff.to_dict()
        ]
        return next_state

    def merge_outputs(
        self, state: dict[str, Any], worker_outputs: dict[str, str]
    ) -> dict[str, Any]:
        next_state = dict(state)
        next_state["worker_outputs"] = worker_outputs
        next_state["merged"] = True
        # Detect duplication (simple: any two outputs identical)
        values = list(worker_outputs.values())
        conflicts = 0
        for i, v1 in enumerate(values):
            for v2 in values[i + 1:]:
                if v1 == v2 and v1:
                    conflicts += 1
        next_state["merge_conflicts"] = conflicts
        return next_state

    def evaluate_coordination(
        self, state: dict[str, Any]
    ) -> CoordinationResult:
        handoffs = state.get("handoffs", [])
        worker_outputs = state.get("worker_outputs", {{}})
        workers_used = len(worker_outputs) or len(self._workers_spec)
        merge_conflicts = state.get("merge_conflicts", 0)

        # Duplication rate
        values = list(worker_outputs.values())
        if len(values) > 1:
            unique = len(set(v for v in values if v))
            total = len([v for v in values if v])
            duplication_rate = (
                1.0 - (unique / max(total, 1)) if total > 0 else 0.0
            )
        else:
            duplication_rate = 0.0

        # Handoff quality (average quality)
        if handoffs:
            avg_handoff = sum(
                h.get("quality", 0.5) for h in handoffs
            ) / len(handoffs)
        else:
            avg_handoff = 0.5

        # Merge quality: fewer conflicts is better
        merge_quality = max(0.0, 1.0 - merge_conflicts * 0.2)

        # Outcome quality: completed actions ratio
        completed = len(state.get("completed_actions", []))
        failed = len(state.get("failed_actions", []))
        outcome_quality = completed / max(completed + failed, 1)

        dup_avoidance = max(0.0, 1.0 - duplication_rate)
        score = round(
            dup_avoidance * 0.25
            + avg_handoff * 0.25
            + merge_quality * 0.25
            + outcome_quality * 0.25,
            4,
        )

        return CoordinationResult(
            score=score,
            reasoning=(
                f"{{workers_used}} workers, {{len(handoffs)}} handoffs, "
                f"duplication rate {{duplication_rate:.2f}}, "
                f"{{merge_conflicts}} merge conflicts."
            ),
            dimension_scores={{
                "duplication_avoidance": round(dup_avoidance, 4),
                "handoff_quality": round(avg_handoff, 4),
                "merge_quality": round(merge_quality, 4),
                "outcome_quality": round(outcome_quality, 4),
            }},
            workers_used=workers_used,
            handoffs_completed=len(handoffs),
            duplication_rate=round(duplication_rate, 4),
            merge_conflicts=merge_conflicts,
        )

    def evaluate_trace(
        self, trace: ActionTrace, final_state: dict[str, Any]
    ) -> SimulationResult:
        coord = self.evaluate_coordination(final_state)
        action_success = trace.success_rate
        score = round(coord.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=coord.reasoning,
            dimension_scores={{
                "duplication_avoidance": coord.dimension_scores.get(
                    "duplication_avoidance", 0.0
                ),
                "handoff_quality": coord.dimension_scores.get(
                    "handoff_quality", 0.0
                ),
                "merge_quality": coord.dimension_scores.get(
                    "merge_quality", 0.0
                ),
                "outcome_quality": coord.dimension_scores.get(
                    "outcome_quality", 0.0
                ),
                "action_success": round(action_success, 4),
            }},
            workflow_complete=final_state.get("merged", False),
            actions_taken=len(trace.records),
            actions_successful=sum(
                1 for r in trace.records if r.result.success
            ),
            recovery_attempts=coord.merge_conflicts,
            rollback_quality=coord.dimension_scores.get(
                "merge_quality", 0.0
            ),
        )

    def get_rubric(self) -> str:
        return (
            "Evaluate on duplication avoidance, handoff quality, "
            "merge quality, and overall outcome quality."
        )

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
