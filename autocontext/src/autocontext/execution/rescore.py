"""Pure revalidation core for on-demand re-score (AC-885 Slice D2a).

Report-only: computes a comparison of a generation's original score/epoch against a fresh re-score, via
an injected ``score_fn`` so this module has no provider/network/DB dependency. Writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

RescoreStatus = Literal["revalidated", "skipped_no_artifact", "skipped_no_active_epoch", "skipped_no_evaluator", "error"]


class _Common(TypedDict):
    original_score: float | None
    original_epoch: str | None
    active_epoch: str | None


@dataclass(frozen=True, slots=True)
class GenerationRevalidation:
    generation_index: int
    status: RescoreStatus
    reason: str
    original_score: float | None
    original_epoch: str | None
    new_score: float | None
    new_epoch: str | None
    active_epoch: str | None
    was_stale: bool
    new_matches_active: bool
    score_delta: float | None


def _was_stale(original_epoch: str | None, active_epoch: str | None) -> bool:
    return original_epoch is not None and active_epoch is not None and original_epoch != active_epoch


def _matches_active(new_epoch: str | None, active_epoch: str | None) -> bool:
    return new_epoch is not None and active_epoch is not None and new_epoch == active_epoch


def _delta(new_score: float | None, original_score: float | None) -> float | None:
    if new_score is None or original_score is None:
        return None
    return new_score - original_score


def _result(
    generation_index: int,
    status: RescoreStatus,
    reason: str,
    original_score: float | None,
    original_epoch: str | None,
    active_epoch: str | None,
    new_score: float | None = None,
    new_epoch: str | None = None,
) -> GenerationRevalidation:
    return GenerationRevalidation(
        generation_index=generation_index,
        status=status,
        reason=reason,
        original_score=original_score,
        original_epoch=original_epoch,
        new_score=new_score,
        new_epoch=new_epoch,
        active_epoch=active_epoch,
        was_stale=_was_stale(original_epoch, active_epoch),
        new_matches_active=_matches_active(new_epoch, active_epoch),
        score_delta=_delta(new_score, original_score),
    )


def revalidate_one(
    generation_index: int,
    original_score: float | None,
    original_epoch: str | None,
    active_epoch: str | None,
    artifact: str | None,
    score_fn: Callable[[str], tuple[float | None, str | None]] | None,
) -> GenerationRevalidation:
    """Re-score one generation's artifact via ``score_fn`` and report the comparison. Writes nothing.

    Skip precedence: no active epoch, then no evaluator (``score_fn`` None), then no artifact. A
    ``score_fn`` that raises yields ``error``; one that returns a None epoch yields ``skipped_no_evaluator``.
    """
    common: _Common = {
        "original_score": original_score,
        "original_epoch": original_epoch,
        "active_epoch": active_epoch,
    }
    if active_epoch is None:
        return _result(generation_index, "skipped_no_active_epoch", "scenario has no active evaluator epoch", **common)
    if score_fn is None:
        return _result(generation_index, "skipped_no_evaluator", "scenario has no reconstructable rubric judge", **common)
    if artifact is None:
        return _result(generation_index, "skipped_no_artifact", "no stored competitor output for this generation", **common)
    try:
        new_score, new_epoch = score_fn(artifact)
    except Exception as exc:  # noqa: BLE001 - report any scorer failure, never crash the command
        return _result(generation_index, "error", f"re-score failed: {exc}", **common)
    if new_epoch is None:
        return _result(generation_index, "skipped_no_evaluator", "scenario evaluator produced no epoch", **common)
    return _result(
        generation_index,
        "revalidated",
        "re-scored under the current evaluator",
        new_score=new_score,
        new_epoch=new_epoch,
        **common,
    )
