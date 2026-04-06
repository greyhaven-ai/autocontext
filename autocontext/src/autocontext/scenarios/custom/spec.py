from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


@dataclass(slots=True)
class StrategyParam:
    name: str
    description: str
    min_value: float = 0.0
    max_value: float = 1.0
    default: float = 0.5


@dataclass(slots=True)
class Constraint:
    expression: str  # e.g. "aggression + defense"
    operator: str  # one of "<=", ">=", "<", ">", "=="
    threshold: float
    description: str


@dataclass(slots=True)
class EnvironmentVariable:
    name: str
    description: str
    low: float = 0.0
    high: float = 1.0


@dataclass(slots=True)
class ScoringComponent:
    name: str
    description: str
    formula_terms: dict[str, float] = field(default_factory=dict)
    noise_range: tuple[float, float] = (-0.05, 0.05)


class ScenarioSpec(BaseModel):
    name: str
    display_name: str
    description: str
    strategy_interface_description: str
    evaluation_criteria: str
    strategy_params: list[StrategyParam] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    environment_variables: list[EnvironmentVariable] = Field(default_factory=list)
    scoring_components: list[ScoringComponent] = Field(default_factory=list)
    final_score_weights: dict[str, float] = Field(default_factory=dict)
    win_threshold: float = 0.55
    observation_constraints: list[str] = Field(default_factory=list)
    scenario_type: str = "parametric"  # "parametric" | "agent_task"
    family: str = ""  # Explicit family name for detect_family (AC-524); empty = structural fallback

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioSpec:
        return cls.model_validate(data)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "spec.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, directory: Path) -> ScenarioSpec:
        path = directory / "spec.json"
        data = json.loads(path.read_text())
        return cls.from_dict(data)
