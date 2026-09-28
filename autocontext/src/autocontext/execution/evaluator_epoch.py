"""Evaluator identity and score-lineage comparisons.

New Python judges use immutable serving specifications via ``EvaluatorEpoch.from_spec``.
The historical rubric/provider/model helper stays byte-stable for legacy records;
its digests are distinct from the new versioned serving contract. Sampling settings
remain execution provenance rather than evaluator identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from autocontext.execution.judge_spec import JudgeServingSpec

EVALUATOR_EPOCH_REBASELINE = "evaluator_epoch_rebaseline"


@dataclass(frozen=True, slots=True)
class EvaluatorEpoch:
    epoch_id: str
    rubric_hash: str
    judge_provider: str
    judge_model: str
    serving_spec: str | None = None

    @classmethod
    def from_spec(cls, spec: JudgeServingSpec) -> EvaluatorEpoch:
        return cls(spec.epoch_id, _sha256(spec.compiled_rubric), spec.judge_provider,
                   spec.judge_model, spec.canonical_json())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_evaluator_epoch(rubric_text: str, judge_provider: str, judge_model: str) -> EvaluatorEpoch:
    """Legacy rubric/provider/model digest. New judges use ``EvaluatorEpoch.from_spec``.

    Kept byte-stable for reading historical records; never upgrades their lineage.
    """
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


def resolve_epoch_rebaseline(
    baseline_epoch: str | None,
    round_epoch: str | None,
    has_baseline: bool,
    *,
    baseline_spec: str | None = None,
    round_spec: str | None = None,
    loop_pinned_dimensions: Sequence[str] | None = None,
) -> EpochBaselineDecision:
    """Decide whether a round's epoch forces the improve loop to re-baseline.

    The first round (``has_baseline`` False) establishes the baseline and never re-baselines. When a
    baseline exists and the round's epoch is not comparable to it, the prior baseline is stale and is
    excluded so the loop re-baselines under the round's epoch.

    One epoch change is exempt by policy: the loop pinning an unpinned judge to the dimension names it
    scored (AC-48). The pins are served, so they mint a new epoch, but every other serving field is
    unchanged and the pins keep later rounds consistent rather than defining a new evaluator. Both
    specifications must reproduce their epochs, the baseline must be unpinned and the round's pins
    must equal ``loop_pinned_dimensions``; anything else re-baselines. Python-only: the TypeScript
    judge still stamps legacy epochs, which do not bind pinned dimensions.
    """
    if (
        not has_baseline
        or are_comparable(baseline_epoch, round_epoch)
        or _only_loop_pinning_differs(
            baseline_epoch=baseline_epoch,
            baseline_spec=baseline_spec,
            round_epoch=round_epoch,
            round_spec=round_spec,
            loop_pinned_dimensions=loop_pinned_dimensions,
        )
    ):
        return EpochBaselineDecision(rebaseline=False, stale_epoch=None)
    return EpochBaselineDecision(rebaseline=True, stale_epoch=baseline_epoch)


def served_pinned_dimensions(spec_json: str | None, epoch: str | None) -> tuple[str, ...]:
    """Dimension names a verified serving specification pinned, in served order; ``()`` when unknown."""
    spec = _verified_spec(spec_json, epoch)
    return spec.pinned_dimensions if spec is not None else ()


def _verified_spec(spec_json: str | None, epoch: str | None) -> JudgeServingSpec | None:
    if spec_json is None or epoch is None:
        return None
    try:
        spec = JudgeServingSpec.model_validate_json(spec_json)
        spec.require_epoch(epoch)
    except ValueError:  # includes pydantic's ValidationError
        return None
    return spec


def _only_loop_pinning_differs(
    *,
    baseline_epoch: str | None,
    baseline_spec: str | None,
    round_epoch: str | None,
    round_spec: str | None,
    loop_pinned_dimensions: Sequence[str] | None,
) -> bool:
    if not loop_pinned_dimensions:
        return False
    baseline = _verified_spec(baseline_spec, baseline_epoch)
    current = _verified_spec(round_spec, round_epoch)
    if baseline is None or current is None or baseline.pinned_dimensions:
        return False
    excluded = {"pinned_dimensions"}
    same_serving = baseline.model_dump(exclude=excluded) == current.model_dump(exclude=excluded)
    return same_serving and current.pinned_dimensions == tuple(loop_pinned_dimensions)
