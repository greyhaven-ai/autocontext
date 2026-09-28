"""The skip rule (design section 4): a threshold on predicted-fail probability, chosen on training data only."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from autocontext.prescreen.dataset import Example

TARGET_PRECISION = 0.95
MIN_COVERED = 20
CALIBRATION_SPLIT = 0.7


class _Model(Protocol):
    def fit(self, train: Sequence[Example]) -> None: ...

    def predict_fail(self, examples: Sequence[Example]) -> list[float]: ...


def skip_threshold(
    p_fail: Sequence[float],
    failed: Sequence[bool],
    *,
    target: float = TARGET_PRECISION,
    min_covered: int = MIN_COVERED,
) -> float | None:
    """Lowest probability cut whose covered rounds (p >= cut, at least min_covered) the judge failed at >= target.

    Cuts fall only between distinct probabilities, so tied rounds are all skipped or all judged. None means no cut
    qualifies, and nothing is skipped.
    """
    if len(p_fail) != len(failed):
        raise ValueError("p_fail and failed must align")
    order = sorted(range(len(p_fail)), key=lambda i: -p_fail[i])
    best: float | None = None
    hits = 0
    for k, i in enumerate(order, start=1):
        hits += int(failed[i])
        tie_ends = k == len(order) or p_fail[order[k]] < p_fail[i]
        if tie_ends and k >= min_covered and hits / k >= target:
            best = p_fail[i]
    return best


def inner_skip_threshold(
    factory: Callable[[], _Model],
    train: Sequence[Example],
    *,
    target: float = TARGET_PRECISION,
    min_covered: int = MIN_COVERED,
    split: float = CALIBRATION_SPLIT,
) -> float | None:
    """Fit on the first 70% of the time-ordered training window and pick the cut on the last 30%; never on test data."""
    cut = int(len(train) * split)
    head, tail = train[:cut], train[cut:]
    if not tail or len({e.failed for e in head}) < 2:
        return None
    model = factory()
    model.fit(head)
    return skip_threshold(model.predict_fail(tail), [e.failed for e in tail], target=target, min_covered=min_covered)
