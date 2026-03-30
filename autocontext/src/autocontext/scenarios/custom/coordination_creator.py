from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import cast

from autocontext.agents.types import LlmFn
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.coordination import CoordinationInterface
from autocontext.scenarios.custom.coordination_codegen import (
    generate_coordination_class,
)
from autocontext.scenarios.custom.coordination_designer import design_coordination
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family_marker

logger = logging.getLogger(__name__)


class CoordinationCreator:
    def __init__(self, llm_fn: LlmFn, knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        spec = design_coordination(description, self.llm_fn)
        errors = validate_for_family("coordination", asdict(spec))
        if errors:
            raise ValueError(f"coordination spec validation failed: {'; '.join(errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        source = generate_coordination_class(spec, name=name)
        source_errors = validate_source_for_family("coordination", source)
        if source_errors:
            raise ValueError(
                f"coordination source validation failed: {'; '.join(source_errors)}"
            )

        (scenario_dir / "scenario.py").write_text(source, encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "scenario_type": get_family_marker("coordination"),
                    "description": spec.description,
                    "environment_description": spec.environment_description,
                    "initial_state_description": spec.initial_state_description,
                    "workers": spec.workers,
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
            get_family_marker("coordination"),
            encoding="utf-8",
        )

        cls = load_custom_scenario(custom_dir, name, CoordinationInterface)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered coordination scenario '%s'", name)
        return cast(ScenarioInterface, cls())
