"""Deadline and cancellation boundary for matched context evaluation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import threading

    from autocontext.context_bundles.models import ContextBundle, MatchedTrial
    from autocontext.context_bundles.promotion import (
        ContextBundleEvaluationOutcome,
        ContextBundleEvaluationUnit,
        ContextBundleEvaluator,
    )


class DeadlineBoundEvaluator(Protocol):
    def evaluate_with_control(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
        *,
        deadline: float | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> ContextBundleEvaluationOutcome: ...


@dataclass(frozen=True, slots=True)
class ContextEvaluationControl:
    deadline: float | None = None
    cancellation_check: Callable[[], bool] | None = None
    cancellation_event: threading.Event | None = None

    @property
    def bounded(self) -> bool:
        return self.deadline is not None or self.cancellation_check is not None or self.cancellation_event is not None

    def cancelled(self) -> bool:
        return bool(
            (self.cancellation_event is not None and self.cancellation_event.is_set())
            or (self.cancellation_check is not None and self.cancellation_check())
        )

    def check(self) -> None:
        if self.cancelled():
            raise RuntimeError("context bundle evaluation was cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("context bundle evaluation deadline was exhausted")

    def evaluate_arm(
        self,
        evaluator: ContextBundleEvaluator,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
    ) -> ContextBundleEvaluationOutcome:
        self.check()
        if self.bounded:
            evaluate = getattr(evaluator, "evaluate_with_control", None)
            if not callable(evaluate):
                raise RuntimeError("context bundle evaluator cannot prove bounded cancellation")
            result = evaluate(
                bundle,
                unit,
                deadline=self.deadline,
                cancellation_check=self.cancelled,
            )
        else:
            result = evaluator.evaluate(bundle, unit)
        self.check()
        return cast("ContextBundleEvaluationOutcome", result)


def evaluate_matched_pair(
    control: ContextEvaluationControl,
    evaluator: ContextBundleEvaluator,
    candidate: ContextBundle,
    incumbent: ContextBundle,
    unit: ContextBundleEvaluationUnit,
    *,
    pair_index: int,
    cohort: str,
    require_plan: Callable[[], None],
) -> MatchedTrial:
    """Evaluate alternating arms with a plan check at every boundary."""

    from autocontext.context_bundles.models import MatchedTrial

    ordered = (candidate, incumbent) if pair_index % 2 == 0 else (incumbent, candidate)
    outcomes: dict[str, ContextBundleEvaluationOutcome] = {}
    for bundle in ordered:
        require_plan()
        outcomes[bundle.digest] = control.evaluate_arm(evaluator, bundle, unit)
    return MatchedTrial(
        candidate_digest=candidate.digest,
        incumbent_digest=incumbent.digest,
        evaluator_epoch=candidate.evaluator_epoch,
        cohort=cohort,
        fixture=unit.fixture,
        fixture_digest=unit.fixture_digest,
        seed=unit.seed,
        lane=unit.lane,
        candidate_score=outcomes[candidate.digest].score,
        incumbent_score=outcomes[incumbent.digest].score,
        candidate_valid=outcomes[candidate.digest].valid,
        incumbent_valid=outcomes[incumbent.digest].valid,
    )


__all__ = ["ContextEvaluationControl", "DeadlineBoundEvaluator", "evaluate_matched_pair"]
