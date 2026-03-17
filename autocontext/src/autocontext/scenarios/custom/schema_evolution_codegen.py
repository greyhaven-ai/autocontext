from __future__ import annotations

import re

from autocontext.scenarios.custom.schema_evolution_spec import SchemaEvolutionSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "SchemaEvolution"


def generate_schema_evolution_class(spec: SchemaEvolutionSpec, name: str) -> str:
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
    mutations_repr = [
        {
            "version": m.version,
            "description": m.description,
            "fields_added": m.fields_added,
            "fields_removed": m.fields_removed,
            "fields_modified": m.fields_modified,
            "breaking": m.breaking,
        }
        for m in spec.mutations
    ]
    required_actions = [action.name for action in spec.actions]
    return f'''from __future__ import annotations

from typing import Any

from autocontext.scenarios.schema_evolution import (
    ContextValidity,
    SchemaEvolutionInterface,
    SchemaEvolutionResult,
    SchemaMutation,
)
from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationResult,
)


class {class_name}(SchemaEvolutionInterface):
    name = {name!r}
    _mutations_spec = {mutations_repr!r}

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
            "schema_version": 1,
            "mutations_applied": [],
            "completed_actions": [],
            "failed_actions": [],
            "assumptions_checked": [],
            "stale_detected": 0,
            "stale_missed": 0,
            "recovery_taken": 0,
            "recovery_successful": 0,
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
            return ActionResult(success=False, output="", state_changes={{}}, error=reason), next_state

        next_state["completed_actions"] = [*state.get("completed_actions", []), action.name]

        # Apply pending mutations based on step progression
        pending = [m for m in self._mutations_spec if m["version"] > state.get("schema_version", 1)]
        if pending:
            m = pending[0]
            mutation = SchemaMutation(
                version=m["version"], description=m["description"],
                fields_added=m["fields_added"], fields_removed=m["fields_removed"],
                fields_modified=m["fields_modified"], breaking=m["breaking"],
            )
            next_state = self.apply_mutation(next_state, mutation)

        return (
            ActionResult(
                success=True,
                output=f"executed {{action.name}} (schema v{{next_state.get('schema_version', 1)}})",
                state_changes={{"schema_version": next_state.get("schema_version", 1)}},
            ),
            next_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        required = set({required_actions!r})
        completed = set(state.get("completed_actions", []))
        max_version = max((m["version"] for m in self._mutations_spec), default=1)
        return (
            required.issubset(completed)
            or state.get("schema_version", 1) >= max_version
            or state.get("step", 0) >= {spec.max_steps}
        )

    def get_mutations(self) -> list[SchemaMutation]:
        return [SchemaMutation.from_dict(m) for m in self._mutations_spec]

    def get_schema_version(self, state: dict[str, Any]) -> int:
        return state.get("schema_version", 1)

    def get_mutation_log(self, state: dict[str, Any]) -> list[SchemaMutation]:
        return [SchemaMutation.from_dict(m) for m in state.get("mutations_applied", [])]

    def apply_mutation(self, state: dict[str, Any], mutation: SchemaMutation) -> dict[str, Any]:
        next_state = dict(state)
        next_state["schema_version"] = mutation.version
        next_state["mutations_applied"] = [*state.get("mutations_applied", []), mutation.to_dict()]
        return next_state

    def check_context_validity(
        self, state: dict[str, Any], assumptions: list[str]
    ) -> list[ContextValidity]:
        version = state.get("schema_version", 1)
        removed_fields: set[str] = set()
        for m in state.get("mutations_applied", []):
            removed_fields.update(m.get("fields_removed", []))

        results: list[ContextValidity] = []
        for assumption in assumptions:
            invalidated = any(field in assumption.lower() for field in removed_fields)
            results.append(ContextValidity(
                assumption=assumption,
                still_valid=not invalidated,
                invalidated_by_version=version if invalidated else None,
            ))
        return results

    def evaluate_adaptation(self, state: dict[str, Any]) -> SchemaEvolutionResult:
        mutations_applied = len(state.get("mutations_applied", []))
        stale_detected = state.get("stale_detected", 0)
        stale_missed = state.get("stale_missed", 0)
        recovery_taken = state.get("recovery_taken", 0)
        recovery_successful = state.get("recovery_successful", 0)
        detection_rate = stale_detected / max(stale_detected + stale_missed, 1)
        recovery_rate = recovery_successful / max(recovery_taken, 1)
        score = round(detection_rate * 0.6 + recovery_rate * 0.4, 4)
        return SchemaEvolutionResult(
            score=score,
            reasoning=f"Detected {{stale_detected}}/{{stale_detected + stale_missed}} stale assumptions.",
            dimension_scores={{"detection": round(detection_rate, 4), "recovery": round(recovery_rate, 4)}},
            mutations_applied=mutations_applied,
            stale_assumptions_detected=stale_detected,
            stale_assumptions_missed=stale_missed,
            recovery_actions_taken=recovery_taken,
            recovery_actions_successful=recovery_successful,
        )

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        adaptation = self.evaluate_adaptation(final_state)
        action_success = trace.success_rate
        score = round(adaptation.score * 0.7 + action_success * 0.3, 4)
        return SimulationResult(
            score=score,
            reasoning=adaptation.reasoning,
            dimension_scores={{
                "detection": adaptation.dimension_scores.get("detection", 0.0),
                "recovery": adaptation.dimension_scores.get("recovery", 0.0),
                "action_success": round(action_success, 4),
            }},
            workflow_complete=adaptation.stale_assumptions_missed == 0,
            actions_taken=len(trace.records),
            actions_successful=sum(1 for r in trace.records if r.result.success),
            recovery_attempts=adaptation.recovery_actions_taken,
            rollback_quality=adaptation.dimension_scores.get("recovery", 0.0),
        )

    def get_rubric(self) -> str:
        return "Evaluate on stale-assumption detection, adaptation to schema changes, and recovery quality."

    def max_steps(self) -> int:
        return {spec.max_steps}
'''
