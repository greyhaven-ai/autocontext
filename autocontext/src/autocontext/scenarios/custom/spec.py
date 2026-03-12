from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass(slots=True)
class ScenarioSpec:
    name: str
    display_name: str
    description: str
    strategy_interface_description: str
    evaluation_criteria: str
    strategy_params: list[StrategyParam] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    environment_variables: list[EnvironmentVariable] = field(default_factory=list)
    scoring_components: list[ScoringComponent] = field(default_factory=list)
    final_score_weights: dict[str, float] = field(default_factory=dict)
    win_threshold: float = 0.55
    observation_constraints: list[str] = field(default_factory=list)
    scenario_type: str = "parametric"  # "parametric" | "agent_task"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "strategy_interface_description": self.strategy_interface_description,
            "evaluation_criteria": self.evaluation_criteria,
            "strategy_params": [
                {
                    "name": p.name, "description": p.description,
                    "min_value": p.min_value, "max_value": p.max_value, "default": p.default,
                }
                for p in self.strategy_params
            ],
            "constraints": [
                {"expression": c.expression, "operator": c.operator, "threshold": c.threshold, "description": c.description}
                for c in self.constraints
            ],
            "environment_variables": [
                {"name": e.name, "description": e.description, "low": e.low, "high": e.high}
                for e in self.environment_variables
            ],
            "scoring_components": [
                {
                    "name": s.name, "description": s.description,
                    "formula_terms": s.formula_terms, "noise_range": list(s.noise_range),
                }
                for s in self.scoring_components
            ],
            "final_score_weights": self.final_score_weights,
            "win_threshold": self.win_threshold,
            "observation_constraints": self.observation_constraints,
            "scenario_type": self.scenario_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioSpec:
        return cls(
            name=data["name"],
            display_name=data["display_name"],
            description=data["description"],
            strategy_interface_description=data["strategy_interface_description"],
            evaluation_criteria=data["evaluation_criteria"],
            strategy_params=[
                StrategyParam(
                    name=p["name"], description=p["description"],
                    min_value=p["min_value"], max_value=p["max_value"], default=p["default"],
                )
                for p in data.get("strategy_params", [])
            ],
            constraints=[
                Constraint(
                    expression=c["expression"], operator=c["operator"],
                    threshold=c["threshold"], description=c["description"],
                )
                for c in data.get("constraints", [])
            ],
            environment_variables=[
                EnvironmentVariable(name=e["name"], description=e["description"], low=e["low"], high=e["high"])
                for e in data.get("environment_variables", [])
            ],
            scoring_components=[
                ScoringComponent(
                    name=s["name"],
                    description=s["description"],
                    formula_terms=s["formula_terms"],
                    noise_range=tuple(s["noise_range"]),  # type: ignore[arg-type]
                )
                for s in data.get("scoring_components", [])
            ],
            final_score_weights=data.get("final_score_weights", {}),
            win_threshold=data.get("win_threshold", 0.55),
            observation_constraints=data.get("observation_constraints", []),
            scenario_type=data.get("scenario_type", "parametric"),
        )

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
