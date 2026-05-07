from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.coordination import CoordinationInterface
from autocontext.scenarios.custom.agent_task_designer import (
    AGENT_TASK_DESIGNER_SYSTEM,
    design_validated_agent_task,
)
from autocontext.scenarios.custom.agent_task_validator import (
    validate_intent,
)
from autocontext.scenarios.custom.classifier_cache import (
    ClassifierCache,
    default_classifier_cache_path,
)
from autocontext.scenarios.custom.classifier_input import (
    build_family_classification_brief,
)
from autocontext.scenarios.custom.creator_registry import FAMILY_CONFIGS, create_for_family
from autocontext.scenarios.custom.family_classifier import (
    classify_scenario_family,
    route_to_family,
)
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
)
from autocontext.scenarios.custom.naming import STOP_WORDS as SHARED_STOP_WORDS
from autocontext.scenarios.custom.naming import derive_name as shared_derive_name
from autocontext.scenarios.families import get_family
from autocontext.scenarios.investigation import InvestigationInterface
from autocontext.scenarios.negotiation import NegotiationInterface
from autocontext.scenarios.operator_loop import OperatorLoopInterface
from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface
from autocontext.scenarios.tool_fragility import ToolFragilityInterface
from autocontext.scenarios.workflow import WorkflowInterface

logger = logging.getLogger(__name__)


def _is_timeout_like_error(exc: Exception) -> bool:
    return "timeout" in str(exc).lower()


class AgentTaskCreator:
    """Orchestrates the full agent task creation pipeline."""

    def __init__(
        self,
        llm_fn: LlmFn,
        knowledge_root: Path,
        *,
        designer_system_prompt: str = AGENT_TASK_DESIGNER_SYSTEM,
        retry_designer_system_prompt: str | None = None,
        description_transform: Callable[[str], str] | None = None,
        retry_spec_predicate: Callable[[Any], bool] | None = None,
    ) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root
        self._designer_system_prompt = designer_system_prompt
        self._retry_designer_system_prompt = retry_designer_system_prompt
        self._description_transform = description_transform
        self._retry_spec_predicate = retry_spec_predicate

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
        design_description = self._description_transform(description) if self._description_transform is not None else description
        if family_name:
            family = get_family(family_name)
        else:
            classification_description = build_family_classification_brief(description)
            cache = ClassifierCache(default_classifier_cache_path(self.knowledge_root))
            classification = classify_scenario_family(
                classification_description,
                llm_fn=self.llm_fn,
                cache=cache,
            )
            family = route_to_family(classification)
        if family.name in FAMILY_CONFIGS:
            logger.info("routing description to %s creator", family.name)
            creator = create_for_family(family.name, self.llm_fn, self.knowledge_root)
            try:
                return creator.create(design_description, name=name)
            except Exception as exc:
                if not _is_timeout_like_error(exc):
                    raise
                logger.warning("%s creator failed on first attempt; retrying once", family.name, exc_info=True)
                return creator.create(design_description, name=name)
        if family.name != "agent_task":
            raise ValueError(f"Scenario family '{family.name}' is not yet supported for custom scaffolding")

        # 1. Design
        logger.info("designing agent task from description")
        spec = design_validated_agent_task(
            design_description,
            self.llm_fn,
            system_prompt=self._designer_system_prompt,
            retry_system_prompt=self._retry_designer_system_prompt,
            retry_spec_predicate=self._retry_spec_predicate,
            intent_description=description,
        )

        # 1.5 Auto-heal: generate synthetic sample_input if needed (AC-309),
        # drop unsatisfiable runtime context keys, and clamp quality_threshold
        # into the validator's (0.0, 1.0] range (AC-585).
        from autocontext.scenarios.custom.spec_auto_heal import (
            heal_spec_quality_threshold,
            heal_spec_runtime_context_requirements,
            heal_spec_sample_input,
        )

        spec = heal_spec_sample_input(spec, description=description)
        spec = heal_spec_runtime_context_requirements(spec)
        spec = heal_spec_quality_threshold(spec)

        # 2. Validate spec
        spec_errors = validate_for_family("agent_task", asdict(spec))
        if spec_errors:
            raise ValueError(f"spec validation failed: {'; '.join(spec_errors)}")

        # 2.5 Validate intent — catch task-family drift early (AC-242)
        intent_errors = validate_intent(description, spec)
        if intent_errors:
            raise ValueError(f"intent validation failed: {'; '.join(intent_errors)}")

        # Steps 3-7 (codegen → validate → save → load → register) are
        # shared with the verbatim solve path (AC-734) so both LLM-designed
        # and operator-supplied specs land through one canonical routine.
        from autocontext.knowledge.verbatim_solve import (
            _compile_and_register_agent_task,
        )

        _compile_and_register_agent_task(
            spec=spec,
            name=name,
            knowledge_root=self.knowledge_root,
        )
        from autocontext.scenarios import SCENARIO_REGISTRY

        cls = SCENARIO_REGISTRY[name]
        instance: AgentTaskInterface = cls()
        return instance
