"""Evaluator protocol — domain-agnostic evaluation contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


class Evaluator(Protocol):
    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        """Evaluate a single candidate. Returns an EvaluationResult."""
        ...
