"""Operator-loop scenario creator (AC-432).

Creates runnable operator-in-the-loop scenarios from plain-language descriptions.
Follows the same pattern as SimulationCreator: design → validate → codegen → persist → load → register.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import cast

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.operator_loop_codegen import generate_operator_loop_class
from autocontext.scenarios.custom.operator_loop_designer import design_operator_loop
from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family_marker
from autocontext.scenarios.operator_loop import OperatorLoopInterface

logger = logging.getLogger(__name__)


def validate_operator_loop_spec(spec: OperatorLoopSpec) -> list[str]:
    return validate_for_family("operator_loop", asdict(spec))


class OperatorLoopCreator:
    def __init__(self, llm_fn: Callable[[str, str], str], knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        # Design spec from NL description
        spec = design_operator_loop(description, self.llm_fn)
        errors = validate_operator_loop_spec(spec)
        if errors:
            raise ValueError(f"operator_loop spec validation failed: {'; '.join(errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        # Generate executable Python source
        source = generate_operator_loop_class(spec, name=name)
        source_errors = validate_source_for_family("operator_loop", source)
        if source_errors:
            raise ValueError(f"operator_loop source validation failed: {'; '.join(source_errors)}")

        # Persist artifacts
        (scenario_dir / "scenario.py").write_text(source, encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "scenario_type": get_family_marker("operator_loop"),
                    "description": spec.description,
                    "environment_description": spec.environment_description,
                    "initial_state_description": spec.initial_state_description,
                    "escalation_policy": spec.escalation_policy,
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
        (scenario_dir / "scenario_type.txt").write_text(
            get_family_marker("operator_loop"), encoding="utf-8",
        )

        # Load and register
        cls = load_custom_scenario(custom_dir, name, OperatorLoopInterface)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered operator_loop scenario '%s'", name)
        return cast(ScenarioInterface, cls())
