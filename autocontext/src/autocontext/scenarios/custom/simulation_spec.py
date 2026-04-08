from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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

class SimulationActionSpecModel(BaseModel):
    name: str
    description: str
    parameters: dict[str, str]
    preconditions: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_raw(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)
        normalized["name"] = _coerce_text(raw.get("name", ""))
        normalized["description"] = _coerce_text(raw.get("description", ""))
        parameters_raw = raw.get("parameters", {})
        normalized["parameters"] = {
            str(key): _coerce_text(value)
            for key, value in parameters_raw.items()
        } if isinstance(parameters_raw, dict) else {}
        postconditions = raw.get("postconditions") or raw.get("post_conditions") or []
        effects = raw.get("effects")
        normalized["effects"] = (
            _coerce_text_list(effects)
            if isinstance(effects, list)
            else _coerce_text_list(postconditions)
        )
        normalized["preconditions"] = _coerce_precondition_list(raw.get("preconditions", []))
        return normalized

    @field_validator("parameters", mode="before")
    @classmethod
    def _coerce_parameters(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): _coerce_text(item) for key, item in value.items()}

    @field_validator("preconditions", mode="before")
    @classmethod
    def _coerce_preconditions(cls, value: Any) -> list[str]:
        return _coerce_precondition_list(value)

    @field_validator("effects", mode="before")
    @classmethod
    def _coerce_effects(cls, value: Any) -> list[str]:
        return _coerce_text_list(value)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SimulationActionSpecModel:
        return cls.model_validate(raw)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


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
