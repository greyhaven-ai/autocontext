from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


class ExecutionEngine(Protocol):
    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute one match in isolated data-plane context."""
