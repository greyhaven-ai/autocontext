from __future__ import annotations

from pathlib import Path

from autocontext.agents.types import LlmFn
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.creator_registry import create_for_family


class FamilyCreatorShim:
    """Compatibility wrapper for legacy per-family creator modules."""

    family: str = ""

    def __init__(self, llm_fn: LlmFn, knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        return create_for_family(self.family, self.llm_fn, self.knowledge_root).create(
            description,
            name=name,
        )
