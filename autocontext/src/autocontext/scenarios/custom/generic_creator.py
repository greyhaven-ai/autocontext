"""Generic scenario creator — replaces 9 per-family creator classes (AC-471).

Instead of CoordinationCreator, InvestigationCreator, etc., use:

    creator = GenericScenarioCreator(
        family="coordination",
        designer_fn=design_coordination,
        codegen_fn=generate_coordination_class,
        interface_class=CoordinationInterface,
        llm_fn=llm_fn,
        knowledge_root=knowledge_root,
    )
    scenario = creator.create("description", "my_scenario")
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from autocontext.agents.types import LlmFn
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family_marker

logger = logging.getLogger(__name__)


class GenericScenarioCreator:
    """Single creator class that handles all scenario families.

    Parameterized by:
    - family: the family name string (e.g. "coordination")
    - designer_fn: (description, llm_fn) -> spec dataclass
    - codegen_fn: (spec, name) -> source string
    - interface_class: the ABC interface to validate against
    """

    def __init__(
        self,
        family: str,
        designer_fn: Callable[[str, LlmFn], Any],
        codegen_fn: Callable[..., str],
        interface_class: type,
        llm_fn: LlmFn,
        knowledge_root: Path,
    ) -> None:
        self.family = family
        self.designer_fn = designer_fn
        self.codegen_fn = codegen_fn
        self.interface_class = interface_class
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        """Design → validate → codegen → persist → load → register."""
        # 1. Design the spec
        spec = self.designer_fn(description, self.llm_fn)

        # 2. Validate spec
        spec_dict = asdict(spec) if hasattr(spec, "__dataclass_fields__") else spec.model_dump()
        errors = validate_for_family(self.family, spec_dict)
        if errors:
            raise ValueError(f"{self.family} spec validation failed: {'; '.join(errors)}")

        # 3. Generate source code
        source = self.codegen_fn(spec, name=name)

        # 4. Validate source
        source_errors = validate_source_for_family(self.family, source)
        if source_errors:
            raise ValueError(
                f"{self.family} source validation failed: {'; '.join(source_errors)}"
            )

        # 5. Persist
        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        (scenario_dir / "scenario.py").write_text(source, encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {"name": name, "scenario_type": get_family_marker(self.family), **spec_dict},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        (scenario_dir / "scenario_type.txt").write_text(
            get_family_marker(self.family),
            encoding="utf-8",
        )

        # 6. Load and register
        cls = load_custom_scenario(custom_dir, name, self.interface_class)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered %s scenario '%s'", self.family, name)
        return cast(ScenarioInterface, cls())
