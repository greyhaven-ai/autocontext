from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import cast

from autocontext.agents.types import LlmFn
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.custom.tool_fragility_codegen import (
    generate_tool_fragility_class,
)
from autocontext.scenarios.custom.tool_fragility_designer import design_tool_fragility
from autocontext.scenarios.families import get_family_marker
from autocontext.scenarios.tool_fragility import ToolFragilityInterface

logger = logging.getLogger(__name__)


class ToolFragilityCreator:
    def __init__(self, llm_fn: LlmFn, knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        spec = design_tool_fragility(description, self.llm_fn)
        errors = validate_for_family("tool_fragility", asdict(spec))
        if errors:
            raise ValueError(f"tool_fragility spec validation failed: {'; '.join(errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        source = generate_tool_fragility_class(spec, name=name)
        source_errors = validate_source_for_family("tool_fragility", source)
        if source_errors:
            raise ValueError(
                f"tool_fragility source validation failed: {'; '.join(source_errors)}"
            )

        (scenario_dir / "scenario.py").write_text(source, encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "scenario_type": get_family_marker("tool_fragility"),
                    "description": spec.description,
                    "environment_description": spec.environment_description,
                    "initial_state_description": spec.initial_state_description,
                    "tool_contracts": [asdict(tc) for tc in spec.tool_contracts],
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
            get_family_marker("tool_fragility"),
            encoding="utf-8",
        )

        cls = load_custom_scenario(custom_dir, name, ToolFragilityInterface)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered tool_fragility scenario '%s'", name)
        return cast(ScenarioInterface, cls())
