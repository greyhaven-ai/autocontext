"""Staged candidate validation — progressive checks with early-exit.

Inspired by AutoKernel's staged correctness pipeline. Candidate artifacts
pass progressively more expensive checks before full evaluation.

Stages:
    0. Syntax    — Parses as valid JSON/Python/structured text (cheap, instant)
    1. Contract  — Matches the scenario or task interface schema
    2. Deterministic — Produces consistent output with a fixed seed
    3. Edge-case — Handles boundary conditions and scenario edge fixtures
    4. Evaluation-ready — Passes minimum executable checks for full evaluation
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StageResult:
    """Result of a single validation stage."""

    stage: int
    name: str
    passed: bool
    duration_ms: float
    error: str | None = None


class ValidationStage(ABC):
    """Abstract base class for a single validation stage.

    Subclasses must implement :pyattr:`name` and :pymeth:`run`.
    """

    def __init__(self, order: int) -> None:
        self._order = order

    @property
    def order(self) -> int:
        """Numeric order in the pipeline (lower runs first)."""
        return self._order

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable stage name."""

    @abstractmethod
    def run(self, candidate: Any, scenario: Any) -> StageResult:
        """Execute this stage against a candidate artifact.

        Args:
            candidate: The artifact to validate (strategy dict, code string, etc.).
            scenario: The scenario or task interface for context.

        Returns:
            StageResult with pass/fail, timing, and optional error message.
        """


class ValidationPipeline:
    """Execute validation stages sequentially with early-exit on failure.

    Stages are sorted by ``order`` and run in ascending order. The pipeline
    stops at the first failing stage — later stages are never invoked.
    """

    def __init__(self, stages: list[ValidationStage]) -> None:
        self._stages = sorted(stages, key=lambda s: s.order)

    def run(self, candidate: Any, scenario: Any) -> list[StageResult]:
        """Run all stages, stopping at the first failure.

        Returns:
            List of StageResult for each stage that ran (including the failing one).
        """
        results: list[StageResult] = []

        for stage in self._stages:
            t0 = time.monotonic()
            try:
                result = stage.run(candidate, scenario)
            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                result = StageResult(
                    stage=stage.order,
                    name=stage.name,
                    passed=False,
                    duration_ms=duration_ms,
                    error=str(exc),
                )

            results.append(result)

            if not result.passed:
                LOGGER.debug(
                    "Validation stopped at stage %d (%s): %s",
                    stage.order, stage.name, result.error,
                )
                break

        return results

    @staticmethod
    def all_passed(results: list[StageResult]) -> bool:
        """Return True if every stage in *results* passed."""
        return all(r.passed for r in results)

    @staticmethod
    def failed_stage(results: list[StageResult]) -> str | None:
        """Return the name of the first failed stage, or None if all passed."""
        for r in results:
            if not r.passed:
                return r.name
        return None
