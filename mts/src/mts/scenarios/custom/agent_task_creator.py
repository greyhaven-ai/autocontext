from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

from mts.scenarios.agent_task import AgentTaskInterface
from mts.scenarios.custom.agent_task_codegen import generate_agent_task_class
from mts.scenarios.custom.agent_task_designer import design_agent_task
from mts.scenarios.custom.agent_task_validator import (
    validate_execution,
    validate_spec,
    validate_syntax,
)
from mts.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR

logger = logging.getLogger(__name__)


class AgentTaskCreator:
    """Orchestrates the full agent task creation pipeline."""

    def __init__(
        self,
        llm_fn: Callable[[str, str], str],
        knowledge_root: Path,
    ) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def derive_name(self, description: str) -> str:
        words = re.sub(r"[^a-z0-9\s]", "", description.lower()).split()
        meaningful = [
            w for w in words
            if w not in {"a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for"}
        ]
        name_words = meaningful[:3] if len(meaningful) >= 3 else meaningful[:2] if meaningful else ["custom"]
        return "_".join(name_words)

    def create(self, description: str) -> AgentTaskInterface:
        """Run the full pipeline: design → validate → codegen → validate → load → register.

        Returns:
            An instance of the generated AgentTaskInterface subclass.
        """
        # 1. Design
        logger.info("designing agent task from description")
        spec = design_agent_task(description, self.llm_fn)

        # 2. Validate spec
        spec_errors = validate_spec(spec)
        if spec_errors:
            raise ValueError(f"spec validation failed: {'; '.join(spec_errors)}")

        # 3. Derive name and generate code
        name = self.derive_name(description)
        logger.info("generating code for agent task '%s'", name)
        source = generate_agent_task_class(spec, name=name)

        # 4. Validate syntax
        syntax_errors = validate_syntax(source)
        if syntax_errors:
            raise ValueError(f"syntax validation failed: {'; '.join(syntax_errors)}")

        # 5. Validate execution
        exec_errors = validate_execution(source)
        if exec_errors:
            raise ValueError(f"execution validation failed: {'; '.join(exec_errors)}")

        # 6. Save to disk
        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        scenario_file = scenario_dir / "agent_task.py"
        scenario_file.write_text(source, encoding="utf-8")

        spec_file = scenario_dir / "agent_task_spec.json"
        spec_data: dict[str, object] = {
            "task_prompt": spec.task_prompt,
            "judge_rubric": spec.judge_rubric,
            "output_format": spec.output_format,
            "judge_model": spec.judge_model,
            "difficulty_tiers": spec.difficulty_tiers,
        }
        if spec.reference_context is not None:
            spec_data["reference_context"] = spec.reference_context
        if spec.reference_sources is not None:
            spec_data["reference_sources"] = spec.reference_sources
        if spec.required_concepts is not None:
            spec_data["required_concepts"] = spec.required_concepts
        if spec.calibration_examples is not None:
            spec_data["calibration_examples"] = spec.calibration_examples
        spec_file.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

        # Mark as agent_task type
        type_file = scenario_dir / "scenario_type.txt"
        type_file.write_text("agent_task", encoding="utf-8")

        # 7. Load and register
        cls = self._load_agent_task(custom_dir, name)
        from mts.scenarios import SCENARIO_REGISTRY
        SCENARIO_REGISTRY[name] = cls  # type: ignore[assignment]
        logger.info("registered agent task '%s'", name)

        return cls()

    def _load_agent_task(self, custom_dir: Path, name: str) -> type[AgentTaskInterface]:
        module_name = f"mts.scenarios.custom.generated.agent_task_{name}"
        source_path = custom_dir / name / "agent_task.py"

        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, str(source_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot create module spec for {source_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, AgentTaskInterface)
                and attr is not AgentTaskInterface
            ):
                return attr  # type: ignore[return-value]

        raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")
