from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.coordination import CoordinationInterface
from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_designer import design_agent_task
from autocontext.scenarios.custom.agent_task_revision import (
    patch_legacy_generated_revise_output,
)
from autocontext.scenarios.custom.agent_task_validator import (
    validate_execution,
    validate_intent,
)
from autocontext.scenarios.custom.artifact_editing_creator import ArtifactEditingCreator
from autocontext.scenarios.custom.coordination_creator import CoordinationCreator
from autocontext.scenarios.custom.family_classifier import (
    classify_scenario_family,
    route_to_family,
)
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.investigation_creator import InvestigationCreator
from autocontext.scenarios.custom.negotiation_creator import NegotiationCreator
from autocontext.scenarios.custom.operator_loop_creator import OperatorLoopCreator
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.custom.schema_evolution_creator import SchemaEvolutionCreator
from autocontext.scenarios.custom.simulation_creator import SimulationCreator
from autocontext.scenarios.custom.tool_fragility_creator import ToolFragilityCreator
from autocontext.scenarios.custom.workflow_creator import WorkflowCreator
from autocontext.scenarios.families import get_family_marker
from autocontext.scenarios.investigation import InvestigationInterface
from autocontext.scenarios.negotiation import NegotiationInterface
from autocontext.scenarios.operator_loop import OperatorLoopInterface
from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface
from autocontext.scenarios.tool_fragility import ToolFragilityInterface
from autocontext.scenarios.workflow import WorkflowInterface

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

    # NOTE: Keep in sync with ts/src/scenarios/agent-task-creator.ts STOP_WORDS
    STOP_WORDS = frozenset({
        "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
        "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
        "that", "can", "should", "could", "would", "will", "must",
        "agent", "tool", "system",
        "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
        "it", "we", "they", "is", "are", "was", "be", "do", "does",
        "to", "in", "on", "at", "by", "which", "what", "how",
    })

    def derive_name(self, description: str) -> str:
        words = re.sub(r"[^a-z0-9\s]", " ", description.lower()).split()
        meaningful = [w for w in words if w not in self.STOP_WORDS]
        # Prefer longer words (>3 chars) as they are more likely domain-specific nouns
        sorted_words = sorted(meaningful, key=len, reverse=True)
        seen: set[str] = set()
        unique: list[str] = []
        for w in sorted_words:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        name_words = unique[:3] if len(unique) >= 3 else unique[:2] if unique else ["custom"]
        return "_".join(name_words)

    def create(
        self,
        description: str,
    ) -> (
        AgentTaskInterface | ScenarioInterface | ArtifactEditingInterface
        | InvestigationInterface | WorkflowInterface
        | SchemaEvolutionInterface | ToolFragilityInterface
        | NegotiationInterface | OperatorLoopInterface
        | CoordinationInterface
    ):
        """Run the full pipeline: design → validate → codegen → validate → load → register.

        Returns:
            An instance of the generated scenario family implementation.
        """
        name = self.derive_name(description)
        classification = classify_scenario_family(description)
        family = route_to_family(classification)
        if family.name == "simulation":
            logger.info("routing description to simulation creator")
            return SimulationCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "artifact_editing":
            logger.info("routing description to artifact-editing creator")
            return ArtifactEditingCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "investigation":
            logger.info("routing description to investigation creator")
            return InvestigationCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "workflow":
            logger.info("routing description to workflow creator")
            return WorkflowCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "schema_evolution":
            logger.info("routing description to schema-evolution creator")
            return SchemaEvolutionCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "tool_fragility":
            logger.info("routing description to tool-fragility creator")
            return ToolFragilityCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "negotiation":
            logger.info("routing description to negotiation creator")
            return NegotiationCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "operator_loop":
            logger.info("routing description to operator-loop creator")
            return OperatorLoopCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name == "coordination":
            logger.info("routing description to coordination creator")
            return CoordinationCreator(self.llm_fn, self.knowledge_root).create(description, name=name)
        if family.name != "agent_task":
            raise ValueError(
                f"Scenario family '{family.name}' is not yet supported for custom scaffolding"
            )

        # 1. Design
        logger.info("designing agent task from description")
        spec = design_agent_task(description, self.llm_fn)

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
        spec_file.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

        # Mark as agent_task type
        type_file = scenario_dir / "scenario_type.txt"
        type_file.write_text(get_family_marker("agent_task"), encoding="utf-8")

        # 7. Load and register
        cls = self._load_agent_task(custom_dir, name)
        from autocontext.scenarios import SCENARIO_REGISTRY
        SCENARIO_REGISTRY[name] = cls  # type: ignore[assignment]
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
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, AgentTaskInterface)
                and attr is not AgentTaskInterface
            ):
                return patch_legacy_generated_revise_output(attr, source_path)  # type: ignore[return-value]

        raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")
