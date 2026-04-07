from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("description", "condition", "name", "action"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return str(value)


def _coerce_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_coerce_text(value) for value in values]


def _coerce_precondition_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("action", "name", "condition", "description"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return str(value)


def _coerce_precondition_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_coerce_precondition_text(value) for value in values]


@dataclass(slots=True)
class SimulationActionSpecModel:
    name: str
    description: str
    parameters: dict[str, str]
    preconditions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SimulationActionSpecModel:
        postconditions = raw.get("postconditions") or raw.get("post_conditions") or []
        effects = raw.get("effects")
        normalized_effects = _coerce_text_list(effects) if isinstance(effects, list) else _coerce_text_list(postconditions)
        parameters_raw = raw.get("parameters", {})
        parameters = {
            str(key): _coerce_text(value)
            for key, value in parameters_raw.items()
        } if isinstance(parameters_raw, dict) else {}
        return cls(
            name=_coerce_text(raw.get("name", "")),
            description=_coerce_text(raw.get("description", "")),
            parameters=parameters,
            preconditions=_coerce_precondition_list(raw.get("preconditions", [])),
            effects=normalized_effects,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "preconditions": list(self.preconditions),
            "effects": list(self.effects),
        }


def parse_simulation_actions(
    raw_actions: list[dict[str, Any]] | list[SimulationActionSpecModel],
) -> list[SimulationActionSpecModel]:
    actions: list[SimulationActionSpecModel] = []
    for action in raw_actions:
        if isinstance(action, SimulationActionSpecModel):
            actions.append(action)
        else:
            actions.append(SimulationActionSpecModel.from_dict(action))
    return actions


def normalize_simulation_spec_dict(spec: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(spec)
    normalized["success_criteria"] = _coerce_text_list(spec.get("success_criteria", []))
    normalized["failure_modes"] = _coerce_text_list(spec.get("failure_modes", []))
    normalized["actions"] = [action.to_dict() for action in parse_simulation_actions(spec.get("actions", []))]
    return normalized


@dataclass(slots=True)
class SimulationSpec:
    description: str
    environment_description: str
    initial_state_description: str
    success_criteria: list[str]
    failure_modes: list[str]
    actions: list[SimulationActionSpecModel]
    max_steps: int = 10
