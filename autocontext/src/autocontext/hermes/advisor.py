"""AC-708 slice 1: curator advisor data layer + baseline + metrics.

Foundation for the local Hermes curator advisor. This slice ships:

* :class:`CuratorDecisionExample` — typed value type loaded from the
  AC-705 ``curator-decisions`` export JSONL.
* :func:`load_curator_examples` — tolerant line-by-line loader: bad
  rows are skipped, not raised (matches the AC-704/706 ingest
  posture so a single corrupt row doesn't abort training).
* :class:`BaselineAdvisor` — always-majority-class predictor; trained
  via :func:`train_baseline`. Establishes the baseline that any
  later trained advisor (slice 2: logistic regression / MLX / CUDA)
  must beat.
* :class:`AdvisorMetrics` — per-label precision/recall, overall
  accuracy, an ``insufficient_data`` flag (AC-708 acceptance:
  "clear 'not enough data' failure mode for small Hermes homes").
* :func:`evaluate` — measures an advisor against held-out examples.

The ML backends and the recommendation surface (AC-709) consume
these types but are out of scope for this slice. Keeping the data
contract first means the backends plug in without redesign.

Initial advisor task (per AC-708 ticket): classify whether a
curator decision should be ``consolidated`` / ``pruned`` /
``archived`` / ``added`` (the labels AC-705 emits with
``confidence: "strong"``). Top-k umbrella ranking and
low-confidence detection are deferred to follow-up slices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Canonical label set: matches the AC-705 export contract. Ordered so
# baseline tie-breaks are deterministic when two labels have equal
# support (alphabetical takes precedence).
CANONICAL_LABELS: tuple[str, ...] = ("added", "archived", "consolidated", "pruned")
_LABEL_SET = frozenset(CANONICAL_LABELS)

# AC-708 acceptance criterion: "a clear 'not enough data' failure mode
# for small Hermes homes". 20 examples is a conservative floor for
# any per-label precision/recall to be meaningful; smaller datasets
# get the metrics back so a consumer can inspect them, but with the
# flag set so they don't act on noise.
INSUFFICIENT_DATA_THRESHOLD = 20


@dataclass(frozen=True, slots=True)
class CuratorDecisionExample:
    """One labeled curator decision, ready to feed into training/eval.

    Mirrors the ``input`` block of AC-705's row schema plus the
    ``label`` field. Non-feature fields (``example_id``, ``source``,
    ``context``) are intentionally dropped here — they're audit
    metadata, not learnable signal.
    """

    skill_name: str
    label: str
    state: str
    provenance: str
    pinned: bool
    use_count: int
    view_count: int
    patch_count: int

    @property
    def activity_count(self) -> int:
        return self.use_count + self.view_count + self.patch_count


@dataclass(frozen=True, slots=True)
class LabelMetrics:
    """Precision/recall/support for a single label."""

    precision: float
    recall: float
    support: int

    def to_dict(self) -> dict[str, Any]:
        return {"precision": self.precision, "recall": self.recall, "support": self.support}


@dataclass(frozen=True, slots=True)
class AdvisorMetrics:
    """Aggregate metrics for a single evaluation run."""

    accuracy: float
    per_label: dict[str, LabelMetrics]
    example_count: int
    insufficient_data: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "per_label": {label: m.to_dict() for label, m in self.per_label.items()},
            "example_count": self.example_count,
            "insufficient_data": self.insufficient_data,
        }


class Advisor(Protocol):
    """Anything that can predict a label from a CuratorDecisionExample."""

    def predict(self, example: CuratorDecisionExample) -> str: ...


@dataclass(frozen=True, slots=True)
class BaselineAdvisor:
    """Always-majority-class advisor.

    Establishes the floor every trained advisor must beat. Ties are
    broken in :data:`CANONICAL_LABELS` order so two training runs
    over the same data produce the same predictor.
    """

    majority_label: str
    label_counts: dict[str, int] = field(default_factory=dict)

    def predict(self, example: CuratorDecisionExample) -> str:  # noqa: ARG002 (unused for baseline)
        return self.majority_label


def train_baseline(examples: list[CuratorDecisionExample]) -> BaselineAdvisor:
    """Pick the majority label with deterministic tie-break."""
    if not examples:
        raise ValueError("no labeled examples; cannot train a baseline advisor")
    counts: dict[str, int] = {}
    for ex in examples:
        counts[ex.label] = counts.get(ex.label, 0) + 1
    # Sort by (-count, canonical_order) so the highest count wins and
    # ties resolve in the canonical order. The lookup defaults to a
    # large index for unknown labels so they never tie-break ahead of
    # a known one.
    canonical_index = {label: i for i, label in enumerate(CANONICAL_LABELS)}
    majority = min(counts.items(), key=lambda kv: (-kv[1], canonical_index.get(kv[0], len(CANONICAL_LABELS))))[0]
    return BaselineAdvisor(majority_label=majority, label_counts=dict(counts))


def evaluate(advisor: Advisor, examples: list[CuratorDecisionExample]) -> AdvisorMetrics:
    """Run ``advisor`` over ``examples``; return per-label + overall metrics."""
    if not examples:
        return AdvisorMetrics(accuracy=0.0, per_label={}, example_count=0, insufficient_data=True)

    # Tally TP / FP / FN per label.
    label_universe = sorted({ex.label for ex in examples} | _LABEL_SET)
    tp = dict.fromkeys(label_universe, 0)
    fp = dict.fromkeys(label_universe, 0)
    fn = dict.fromkeys(label_universe, 0)
    support = dict.fromkeys(label_universe, 0)
    correct = 0

    for ex in examples:
        pred = advisor.predict(ex)
        support[ex.label] = support.get(ex.label, 0) + 1
        if pred == ex.label:
            correct += 1
            tp[pred] = tp.get(pred, 0) + 1
        else:
            fp[pred] = fp.get(pred, 0) + 1
            fn[ex.label] = fn.get(ex.label, 0) + 1

    per_label: dict[str, LabelMetrics] = {}
    for label in label_universe:
        if support[label] == 0 and tp.get(label, 0) == 0 and fp.get(label, 0) == 0:
            # Label has no presence in either ground truth or
            # predictions; skip the noise from the output.
            continue
        predicted = tp.get(label, 0) + fp.get(label, 0)
        precision = (tp.get(label, 0) / predicted) if predicted > 0 else 0.0
        recall = (tp.get(label, 0) / support[label]) if support[label] > 0 else 0.0
        per_label[label] = LabelMetrics(precision=precision, recall=recall, support=support[label])

    accuracy = correct / len(examples)
    return AdvisorMetrics(
        accuracy=accuracy,
        per_label=per_label,
        example_count=len(examples),
        insufficient_data=len(examples) < INSUFFICIENT_DATA_THRESHOLD,
    )


def load_curator_examples(path: Path) -> list[CuratorDecisionExample]:
    """Load AC-705 curator-decisions JSONL into typed examples.

    Per-line tolerant: malformed JSON, missing required fields, and
    unknown labels skip the row rather than aborting the load. This
    matches the AC-704 / AC-706 ingest posture so one bad row doesn't
    block training.
    """
    if not path.exists():
        return []
    examples: list[CuratorDecisionExample] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        example = _example_from_row(row)
        if example is not None:
            examples.append(example)
    return examples


def _example_from_row(row: Any) -> CuratorDecisionExample | None:
    if not isinstance(row, dict):
        return None
    label = row.get("label")
    if not isinstance(label, str) or label not in _LABEL_SET:
        return None
    features = row.get("input")
    if not isinstance(features, dict):
        return None
    skill_name = features.get("skill_name")
    if not isinstance(skill_name, str):
        return None
    # PR #972 review (P2): numeric features may arrive as non-numeric
    # strings (e.g. a Hermes export with a corrupted column). Skip the
    # row rather than abort the loader so per-line tolerance matches
    # the AC-704 / AC-706 ingest posture.
    use_count = _as_int(features.get("skill_use_count"))
    view_count = _as_int(features.get("skill_view_count"))
    patch_count = _as_int(features.get("skill_patch_count"))
    if use_count is None or view_count is None or patch_count is None:
        return None
    return CuratorDecisionExample(
        skill_name=skill_name,
        label=label,
        state=str(features.get("skill_state") or "unknown"),
        provenance=str(features.get("skill_provenance") or "unknown"),
        pinned=bool(features.get("skill_pinned", False)),
        use_count=use_count,
        view_count=view_count,
        patch_count=patch_count,
    )


def _as_int(value: Any) -> int | None:
    """Coerce a JSON value to int; return None when the value cannot be
    parsed (non-numeric string, list, dict, etc.). ``None`` from the
    source means "0" (the AC-705 export uses None for "no telemetry").
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


__all__ = [
    "CANONICAL_LABELS",
    "INSUFFICIENT_DATA_THRESHOLD",
    "Advisor",
    "AdvisorMetrics",
    "BaselineAdvisor",
    "CuratorDecisionExample",
    "LabelMetrics",
    "evaluate",
    "load_curator_examples",
    "train_baseline",
]
