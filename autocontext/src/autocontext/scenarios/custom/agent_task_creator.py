from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.coordination import CoordinationInterface
from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_designer import design_agent_task
from autocontext.scenarios.custom.agent_task_revision import (
    patch_legacy_generated_evaluate_output,
    patch_legacy_generated_revise_output,
)
from autocontext.scenarios.custom.agent_task_validator import (
    validate_execution,
    validate_intent,
)
from autocontext.scenarios.custom.creator_registry import FAMILY_CONFIGS, create_for_family
from autocontext.scenarios.custom.family_classifier import (
    classify_scenario_family,
    route_to_family,
)
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.naming import STOP_WORDS as SHARED_STOP_WORDS
from autocontext.scenarios.custom.naming import derive_name as shared_derive_name
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family, get_family_marker
from autocontext.scenarios.investigation import InvestigationInterface
from autocontext.scenarios.negotiation import NegotiationInterface
from autocontext.scenarios.operator_loop import OperatorLoopInterface
from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface
from autocontext.scenarios.tool_fragility import ToolFragilityInterface
from autocontext.scenarios.workflow import WorkflowInterface
from autocontext.util.json_io import write_json

logger = logging.getLogger(__name__)


class AgentTaskCreator:
    """Orchestrates the full agent task creation pipeline."""

    def __init__(
        self,
        llm_fn: LlmFn,
        knowledge_root: Path,
    ) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    STOP_WORDS = SHARED_STOP_WORDS

    def derive_name(self, description: str) -> str:
        return shared_derive_name(description, self.STOP_WORDS)

    def create(
        self,
        description: str,
        *,
        family_name: str = "",
    ) -> (
        AgentTaskInterface
        | ScenarioInterface
        | ArtifactEditingInterface
        | InvestigationInterface
        | WorkflowInterface
        | SchemaEvolutionInterface
        | ToolFragilityInterface
        | NegotiationInterface
        | OperatorLoopInterface
        | CoordinationInterface
    ):
        """Run the full pipeline: design → validate → codegen → validate → load → register.

        Returns:
            An instance of the generated scenario family implementation.
        """
        name = self.derive_name(description)
        if family_name:
            family = get_family(family_name)
        else:
            classification = classify_scenario_family(description)
            family = route_to_family(classification)
        if family.name in FAMILY_CONFIGS:
            logger.info("routing description to %s creator", family.name)
            creator = create_for_family(family.name, self.llm_fn, self.knowledge_root)
            return creator.create(description, name=name)
        if family.name != "agent_task":
            raise ValueError(f"Scenario family '{family.name}' is not yet supported for custom scaffolding")

        # 1. Design
        logger.info("designing agent task from description")
        try:
            spec = design_agent_task(description, self.llm_fn)
        except Exception:
            logger.warning("agent task design failed on first attempt; retrying once", exc_info=True)
            spec = design_agent_task(description, self.llm_fn)

        # 1.5 Auto-heal: generate synthetic sample_input if needed (AC-309)
        from autocontext.scenarios.custom.spec_auto_heal import (
            heal_spec_runtime_context_requirements,
            heal_spec_sample_input,
        )

        spec = heal_spec_sample_input(spec, description=description)
        spec = heal_spec_runtime_context_requirements(spec)

        # 2. Validate spec
        spec_errors = validate_for_family("agent_task", asdict(spec))
        if spec_errors:
            raise ValueError(f"spec validation failed: {'; '.join(spec_errors)}")

        # 2.5 Validate intent — catch task-family drift early (AC-242)
        intent_errors = validate_intent(description, spec)
        if intent_errors:
            raise ValueError(f"intent validation failed: {'; '.join(intent_errors)}")

        # 3. Derive name and generate code
        logger.info("generating code for agent task '%s'", name)
        source = generate_agent_task_class(spec, name=name)

        # 4. Validate generated source through the family pipeline
        source_errors = validate_source_for_family("agent_task", source)
        if source_errors:
            raise ValueError(f"source validation failed: {'; '.join(source_errors)}")

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
        spec_data: dict[str, Any] = {
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
        if spec.context_preparation is not None:
            spec_data["context_preparation"] = spec.context_preparation
        if spec.required_context_keys is not None:
            spec_data["required_context_keys"] = spec.required_context_keys
        if spec.max_rounds != 1:
            spec_data["max_rounds"] = spec.max_rounds
        if spec.quality_threshold != 0.9:
            spec_data["quality_threshold"] = spec.quality_threshold
        if spec.revision_prompt is not None:
            spec_data["revision_prompt"] = spec.revision_prompt
        if spec.sample_input is not None:
            spec_data["sample_input"] = spec.sample_input
        write_json(spec_file, spec_data)

        # Mark as agent_task type
        type_file = scenario_dir / "scenario_type.txt"
        type_file.write_text(get_family_marker("agent_task"), encoding="utf-8")

        # 7. Load and register
        cls = self._load_agent_task(custom_dir, name)
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY[name] = cls
        logger.info("registered agent task '%s'", name)

        return cls()

    def _load_agent_task(self, custom_dir: Path, name: str) -> type[AgentTaskInterface]:
        module_name = f"autocontext.scenarios.custom.generated.agent_task_{name}"
        source_path = custom_dir / name / "agent_task.py"

        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, str(source_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot create module spec for {source_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, AgentTaskInterface) and attr is not AgentTaskInterface:
                attr = patch_legacy_generated_evaluate_output(attr, source_path)
                return patch_legacy_generated_revise_output(attr, source_path)

        raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")
