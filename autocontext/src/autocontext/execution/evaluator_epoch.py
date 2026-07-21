"""Evaluator-epoch identity: a content hash of the rubric + judge that produced a score.

Two scores are comparable only when their epochs are equal. Changing the rubric text, the judge
provider, or the judge model mints a new (non-comparable) epoch; sampling config does not (that is
within-epoch variance owned by the AC-881 noise-calibration layer). This generalizes the ambient
`eval_fingerprint` mechanism to the main LLM-judge path. See docs/internal/ac-885-evaluator-epochs-design.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

EVALUATOR_EPOCH_REBASELINE = "evaluator_epoch_rebaseline"


@dataclass(frozen=True, slots=True)
class EvaluatorEpoch:
    epoch_id: str
    rubric_hash: str
    judge_provider: str
    judge_model: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_evaluator_epoch(rubric_text: str, judge_provider: str, judge_model: str) -> EvaluatorEpoch:
    """Return the epoch for an evaluator. ``epoch_id`` is stable across processes and languages."""
    rubric_hash = _sha256(rubric_text)
    canonical = json.dumps(
        {"judge_model": judge_model, "judge_provider": judge_provider, "rubric_hash": rubric_hash},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return EvaluatorEpoch(
        epoch_id=_sha256(canonical),
        rubric_hash=rubric_hash,
        judge_provider=judge_provider,
        judge_model=judge_model,
    )


def are_comparable(a: str | None, b: str | None) -> bool:
    """Two epoch ids are comparable only when equal; ``None`` (legacy/unknown) equals only ``None``."""
    return a == b


EpochLineageStatus = Literal["current", "stale", "unknown", "no_active_epoch"]


def classify_epoch_lineage(row_epoch: str | None, active_epoch: str | None) -> EpochLineageStatus:
    """Classify a score row's epoch against the scenario's active epoch.

    ``no_active_epoch``: the scenario has no promoted active epoch (nothing to compare against, e.g. a
    game/no-judge run). ``unknown``: an active epoch exists but the row has no lineage (legacy/pre-slice
    row); not asserted stale. ``current`` / ``stale``: both epochs known, equal vs different.
    """
    if active_epoch is None:
        return "no_active_epoch"
    if row_epoch is None:
        return "unknown"
    return "current" if are_comparable(row_epoch, active_epoch) else "stale"


@dataclass(frozen=True, slots=True)
class EpochBaselineDecision:
    rebaseline: bool
    stale_epoch: str | None


def resolve_epoch_rebaseline(baseline_epoch: str | None, round_epoch: str | None, has_baseline: bool) -> EpochBaselineDecision:
    """Decide whether a round's epoch forces the improve loop to re-baseline.

    The first round (``has_baseline`` False) establishes the baseline and never re-baselines. When a
    baseline exists and the round's epoch is not comparable to it, the prior baseline is stale and is
    excluded so the loop re-baselines under the round's epoch.
    """
    if not has_baseline or are_comparable(baseline_epoch, round_epoch):
        return EpochBaselineDecision(rebaseline=False, stale_epoch=None)
    return EpochBaselineDecision(rebaseline=True, stale_epoch=baseline_epoch)
