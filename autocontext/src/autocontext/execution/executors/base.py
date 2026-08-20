from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)


class ExecutionEngine(Protocol):
    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute one match in isolated data-plane context."""

    def execute_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute from a caller-attested initial state without rematerializing it."""
