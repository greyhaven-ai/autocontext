from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.operator_loop_codegen import (
    OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED,
)

logger = logging.getLogger(__name__)


class OperatorLoopCreator:
    def __init__(self, llm_fn: Callable[[str, str], str], knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    def create(self, description: str, name: str) -> ScenarioInterface:
        del description, name
        logger.info("operator_loop runtime scaffolding is intentionally disabled")
        raise NotImplementedError(OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED)
