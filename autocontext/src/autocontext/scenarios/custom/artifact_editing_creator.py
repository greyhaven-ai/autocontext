from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import cast

from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
from autocontext.scenarios.custom.artifact_editing_codegen import generate_artifact_editing_class
from autocontext.scenarios.custom.artifact_editing_designer import design_artifact_editing
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family_marker

logger = logging.getLogger(__name__)


class ArtifactEditingCreator:
    def __init__(self, llm_fn: Callable[[str, str], str], knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ArtifactEditingInterface:
        spec = design_artifact_editing(description, self.llm_fn)
        errors = validate_for_family("artifact_editing", asdict(spec))
        if errors:
            raise ValueError(f"artifact-editing spec validation failed: {'; '.join(errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        source = generate_artifact_editing_class(spec, name=name)
        source_errors = validate_source_for_family("artifact_editing", source)
        if source_errors:
            raise ValueError(
                f"artifact-editing source validation failed: {'; '.join(source_errors)}"
            )

        (scenario_dir / "scenario.py").write_text(source, encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "scenario_type": get_family_marker("artifact_editing"),
                    "task_description": spec.task_description,
                    "rubric": spec.rubric,
                    "validation_rules": spec.validation_rules,
                    "artifacts": [
                        {
                            "path": artifact.path,
                            "content": artifact.content,
                            "content_type": artifact.content_type,
                            "metadata": artifact.metadata,
                        }
                        for artifact in spec.artifacts
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (scenario_dir / "scenario_type.txt").write_text(
            get_family_marker("artifact_editing"),
            encoding="utf-8",
        )

        cls = load_custom_scenario(custom_dir, name, ArtifactEditingInterface)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered artifact-editing scenario '%s'", name)
        return cast(ArtifactEditingInterface, cls())
