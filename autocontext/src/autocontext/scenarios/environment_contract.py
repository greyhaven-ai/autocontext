from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

ScenarioEnvironmentHookKind = Literal[
    "setup",
    "reset",
    "rollout",
    "verification",
    "scoring",
    "replay",
    "evidence",
    "cleanup",
]

HOOK_KINDS = tuple(get_args(ScenarioEnvironmentHookKind))


@dataclass(frozen=True, slots=True)
class ScenarioEnvironmentHook:
    """One callable or reported stage in a scenario environment lifecycle."""

    kind: ScenarioEnvironmentHookKind
    label: str
    description: str
    required: bool = True
    inputs: list[str] = field(default_factory=list)
    emits: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in HOOK_KINDS:
            raise ValueError(f"unknown scenario environment hook kind: {self.kind}")

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> ScenarioEnvironmentHook:
        _reject_extra(data, {"kind", "label", "description", "required", "inputs", "emits", "evidence_refs"})
        return cls(
            kind=data["kind"],
            label=str(data["label"]),
            description=str(data["description"]),
            required=bool(data.get("required", True)),
            inputs=_list_of_str(data.get("inputs", [])),
            emits=_list_of_str(data.get("emits", [])),
            evidence_refs=_list_of_str(data.get("evidence_refs", [])),
        )

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "required": self.required,
            "inputs": list(self.inputs),
            "emits": list(self.emits),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ScenarioEnvironmentHooks:
    """Resettable, verifiable lifecycle hooks expected from serious scenarios."""

    setup: list[ScenarioEnvironmentHook]
    reset: list[ScenarioEnvironmentHook]
    rollout: list[ScenarioEnvironmentHook]
    verification: list[ScenarioEnvironmentHook]
    scoring: list[ScenarioEnvironmentHook]
    replay: list[ScenarioEnvironmentHook]
    evidence: list[ScenarioEnvironmentHook]
    cleanup: list[ScenarioEnvironmentHook]

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> ScenarioEnvironmentHooks:
        _reject_extra(data, set(HOOK_KINDS))
        return cls(**{kind: _hooks(data[kind]) for kind in HOOK_KINDS})

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {kind: [hook.model_dump(mode=mode) for hook in getattr(self, kind)] for kind in HOOK_KINDS}


@dataclass(frozen=True, slots=True)
class ScenarioEnvironmentContract:
    """Portable scenario environment contract shared by Python and TypeScript."""

    scenario_name: str
    scenario_family: str
    hooks: ScenarioEnvironmentHooks
    schema_version: Literal[1] = 1

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> ScenarioEnvironmentContract:
        _reject_extra(data, {"schema_version", "scenario_name", "scenario_family", "hooks"})
        if data.get("schema_version") != 1:
            raise ValueError("scenario environment contract schema_version must be 1")
        hooks = data["hooks"]
        if not isinstance(hooks, dict):
            raise TypeError("hooks must be an object")
        return cls(
            scenario_name=str(data["scenario_name"]),
            scenario_family=str(data["scenario_family"]),
            hooks=ScenarioEnvironmentHooks.model_validate(hooks),
            schema_version=1,
        )

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_name": self.scenario_name,
            "scenario_family": self.scenario_family,
            "hooks": self.hooks.model_dump(mode=mode),
        }


def _reject_extra(data: dict[str, Any], allowed: set[str]) -> None:
    extra = set(data) - allowed
    if extra:
        raise ValueError(f"unexpected scenario environment contract field(s): {', '.join(sorted(extra))}")


def _list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected list")
    return [str(item) for item in value]


def _hooks(value: Any) -> list[ScenarioEnvironmentHook]:
    if not isinstance(value, list):
        raise TypeError("hook group must be a list")
    return [ScenarioEnvironmentHook.model_validate(item) for item in value]


def _hook(
    kind: ScenarioEnvironmentHookKind,
    label: str,
    description: str,
    *,
    inputs: list[str] | None = None,
    emits: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> ScenarioEnvironmentHook:
    return ScenarioEnvironmentHook(
        kind=kind,
        label=label,
        description=description,
        inputs=inputs or [],
        emits=emits or [],
        evidence_refs=evidence_refs or [],
    )


def _contract(
    scenario_name: str,
    scenario_family: str,
    hooks: dict[str, list[ScenarioEnvironmentHook]],
) -> ScenarioEnvironmentContract:
    return ScenarioEnvironmentContract(
        scenario_name=scenario_name,
        scenario_family=scenario_family,
        hooks=ScenarioEnvironmentHooks(**{kind: hooks[kind] for kind in HOOK_KINDS}),
    )


def scenario_environment_contract_for_game(scenario: Any, *, scenario_family: str = "game") -> ScenarioEnvironmentContract:
    """Describe the existing game ScenarioInterface as a resettable harness."""

    return _contract(
        str(getattr(scenario, "name", scenario.__class__.__name__)),
        scenario_family,
        {
            "setup": [
                _hook(
                    "setup",
                    "seeded initial state",
                    "initial_state(seed) creates the deterministic harness state.",
                    inputs=["seed"],
                    emits=["state"],
                )
            ],
            "reset": [
                _hook(
                    "reset",
                    "repeatable reset",
                    "Calling initial_state(seed) again restores a clean state for replay.",
                    inputs=["seed"],
                    emits=["state"],
                )
            ],
            "rollout": [
                _hook(
                    "rollout",
                    "strategy rollout",
                    "validate_actions and step execute a candidate strategy in the harness.",
                    inputs=["state", "strategy"],
                    emits=["next_state"],
                )
            ],
            "verification": [
                _hook(
                    "verification",
                    "action and terminal checks",
                    "validate_actions, is_terminal, and get_result reject invalid or incomplete runs.",
                    inputs=["state", "strategy"],
                    emits=["validation_errors", "terminal_state"],
                    evidence_refs=["Result.validation_errors"],
                )
            ],
            "scoring": [
                _hook(
                    "scoring",
                    "scalar result score",
                    "get_result emits the scalar score consumed by tournaments and reports.",
                    inputs=["terminal_state"],
                    emits=["scalar_score"],
                )
            ],
            "replay": [
                _hook(
                    "replay",
                    "replay timeline",
                    "Result.replay and replay_to_narrative preserve the run trace for inspection.",
                    inputs=["result.replay"],
                    emits=["replay_timeline"],
                )
            ],
            "evidence": [
                _hook(
                    "evidence",
                    "metrics and validation evidence",
                    "Result.summary, Result.metrics, and validation errors explain the score.",
                    inputs=["result"],
                    emits=["summary", "metrics", "validation_errors"],
                )
            ],
            "cleanup": [
                _hook(
                    "cleanup",
                    "in-memory cleanup",
                    "Default game scenarios do not retain external resources between seeded runs.",
                    emits=["no_external_state"],
                )
            ],
        },
    )


def agent_task_template_environment_contract(template_name: str) -> ScenarioEnvironmentContract:
    """Default contract for judge-backed agent-task templates."""

    return _contract(
        template_name,
        "agent_task",
        {
            "setup": [
                _hook(
                    "setup",
                    "template task load",
                    "Load task prompt, rubric, optional sample input, and reference context.",
                    emits=["task_prompt", "judge_rubric"],
                )
            ],
            "reset": [
                _hook(
                    "reset",
                    "seeded task state",
                    "initial_state(seed) creates a clean task state for another attempt.",
                    inputs=["seed"],
                    emits=["state"],
                )
            ],
            "rollout": [
                _hook(
                    "rollout",
                    "agent output attempt",
                    "The candidate model produces one output for the task prompt.",
                    inputs=["task_prompt"],
                    emits=["agent_output"],
                )
            ],
            "verification": [
                _hook(
                    "verification",
                    "judge rubric check",
                    "LLM judge evaluates the output against the rubric and guardrails.",
                    inputs=["agent_output", "judge_rubric"],
                    emits=["judge_result"],
                    evidence_refs=["AgentTaskResult.reasoning"],
                )
            ],
            "scoring": [
                _hook(
                    "scoring",
                    "judge scalar score",
                    "AgentTaskResult.score is the scalar quality score.",
                    inputs=["judge_result"],
                    emits=["scalar_score"],
                )
            ],
            "replay": [
                _hook(
                    "replay",
                    "attempt transcript",
                    "Prompt, output, and judge feedback are enough to replay the attempt.",
                    inputs=["task_prompt", "agent_output", "judge_result"],
                    emits=["attempt_transcript"],
                )
            ],
            "evidence": [
                _hook(
                    "evidence",
                    "judge feedback evidence",
                    "Judge reasoning and dimension scores explain why the output passed or failed.",
                    inputs=["judge_result"],
                    emits=["judge_reasoning", "dimension_scores"],
                )
            ],
            "cleanup": [
                _hook(
                    "cleanup",
                    "stateless template cleanup",
                    "Template-backed tasks keep no external mutable state by default.",
                    emits=["no_external_state"],
                )
            ],
        },
    )


__all__ = [
    "HOOK_KINDS",
    "ScenarioEnvironmentContract",
    "ScenarioEnvironmentHook",
    "ScenarioEnvironmentHookKind",
    "ScenarioEnvironmentHooks",
    "agent_task_template_environment_contract",
    "scenario_environment_contract_for_game",
]
