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
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class StageStatus(StrEnum):
    """Outcome of a validation stage."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageResult:
    """Result of a single validation stage."""

    stage: int
    name: str
    status: StageStatus
    duration_ms: float
    error: str | None = None
    error_code: str | None = None

    @property
    def passed(self) -> bool:
        """Convenience: True when status is PASSED."""
        return self.status is StageStatus.PASSED


class ValidationStage(ABC):
    """Abstract base class for a single validation stage.

    Subclasses must implement :pyattr:`name` and :pymeth:`run`.
    Stages are pure and composable: ``run`` must not own persistence,
    retries, or event emission.
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
            StageResult with status, timing, and optional error details.
        """


class ValidationPipeline:
    """Execute validation stages sequentially with early-exit on failure.

    Stages are sorted by ``order`` and run in ascending order. The pipeline
    stops at the first failing stage — later stages are never invoked.
    Skipped stages (status=SKIPPED) do *not* trigger early-exit.

    An empty pipeline (no stages) is explicitly valid and returns an empty
    result list.  ``all_passed([])`` returns ``True`` — vacuous truth.

    Duplicate stage orders are allowed; stages sharing the same order run
    in their original insertion sequence (stable sort).
    """

    def __init__(self, stages: list[ValidationStage]) -> None:
        self._stages = sorted(stages, key=lambda s: s.order)

    def run(self, candidate: Any, scenario: Any) -> list[StageResult]:
        """Run all stages, stopping at the first failure.

        Returns:
            List of StageResult for each stage that ran (including the failing one).
            Skipped stages are included but do not halt the pipeline.
        """
        results: list[StageResult] = []

        for stage in self._stages:
            t0 = time.monotonic()
            try:
                result = stage.run(candidate, scenario)
            except Exception as exc:
                logger.debug("harness.validation.staged: caught Exception", exc_info=True)
                duration_ms = (time.monotonic() - t0) * 1000
                result = StageResult(
                    stage=stage.order,
                    name=stage.name,
                    status=StageStatus.FAILED,
                    duration_ms=duration_ms,
                    error=str(exc),
                    error_code="stage_exception",
                )

            results.append(result)

            if result.status is StageStatus.FAILED:
                logger.debug(
                    "Validation stopped at stage %d (%s): %s",
                    stage.order, stage.name, result.error,
                )
                break
            # SKIPPED stages do not halt the pipeline

        return results

    @staticmethod
    def all_passed(results: Sequence[StageResult]) -> bool:
        """Return True if no stage failed (vacuous truth for empty list)."""
        return not any(r.status is StageStatus.FAILED for r in results)

    @staticmethod
    def failed_stage(results: Sequence[StageResult]) -> str | None:
        """Return the name of the first failed stage, or None if none failed."""
        for r in results:
            if r.status is StageStatus.FAILED:
                return r.name
        return None
