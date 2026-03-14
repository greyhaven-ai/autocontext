from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.custom.simulation_codegen import generate_simulation_class
from autocontext.scenarios.custom.simulation_designer import design_simulation
from autocontext.scenarios.custom.simulation_spec import SimulationSpec

logger = logging.getLogger(__name__)


def should_use_simulation_family(description: str) -> bool:
    lowered = description.lower()
    keywords = (
        "stateful", "simulation", "workflow", "orchestration", "api",
        "rollback", "retry", "cancellation", "transaction", "debug",
        "diagnos", "evidence", "side effect",
    )
    return any(keyword in lowered for keyword in keywords)


def validate_simulation_spec(spec: SimulationSpec) -> list[str]:
    errors: list[str] = []
    if not spec.description.strip():
        errors.append("description is required")
    if not spec.environment_description.strip():
        errors.append("environment_description is required")
    if len(spec.actions) < 2:
        errors.append("simulation must define at least two actions")
    names = [action.name for action in spec.actions]
    if len(names) != len(set(names)):
        errors.append("action names must be unique")
    if spec.max_steps <= 0:
        errors.append("max_steps must be positive")
    return errors


class SimulationCreator:
    def __init__(self, llm_fn: Callable[[str, str], str], knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        spec = design_simulation(description, self.llm_fn)
        errors = validate_simulation_spec(spec)
        if errors:
            raise ValueError(f"simulation spec validation failed: {'; '.join(errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        (scenario_dir / "scenario.py").write_text(generate_simulation_class(spec, name=name), encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "scenario_type": "simulation",
                    "description": spec.description,
                    "environment_description": spec.environment_description,
                    "initial_state_description": spec.initial_state_description,
                    "success_criteria": spec.success_criteria,
                    "failure_modes": spec.failure_modes,
                    "max_steps": spec.max_steps,
                    "actions": [
                        {
                            "name": action.name,
                            "description": action.description,
                            "parameters": action.parameters,
                            "preconditions": action.preconditions,
                            "effects": action.effects,
                        }
                        for action in spec.actions
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (scenario_dir / "scenario_type.txt").write_text("simulation", encoding="utf-8")

        cls = load_custom_scenario(custom_dir, name)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered simulation scenario '%s'", name)
        return cls()
