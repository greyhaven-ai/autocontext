"""AC-708 slice 2a: pure-Python logistic-regression curator advisor.

Trained advisor that lives alongside :class:`BaselineAdvisor` (the
floor every trained backend must beat). Pure Python — no numpy, no
sklearn, no GPU deps — so the trained backend runs in CI smoke
mode against fixture-sized data. MLX and CUDA backends are
slice-2b/2c follow-ups behind the same :class:`Advisor` Protocol.

Algorithm:

* Multinomial logistic regression trained via gradient descent on
  softmax cross-entropy loss.
* Fixed feature encoder (DRY: every advisor consumes the same
  encoded vector): one-hot ``state``, one-hot ``provenance``,
  ``pinned`` as binary, ``log1p`` of ``use_count`` /
  ``view_count`` / ``patch_count``.
* Stable label order (``CANONICAL_LABELS``) so save/load round-trips
  produce identical predictions.

Checkpoint format (JSON):

    {
      "kind": "logistic_regression",
      "version": 1,
      "labels": ["added", "archived", "consolidated", "pruned"],
      "feature_names": ["state_active", ..., "log1p_use_count"],
      "weights": [[...], ...],         # one row per label
      "intercepts": [..., ..., ...],
      "trained_on": <int>,
      "seed": <int>,
      "epochs": <int>,
      "learning_rate": <float>,
    }
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.hermes.advisor import (
    CANONICAL_LABELS,
    CuratorDecisionExample,
    SkillFeatures,
)

_CHECKPOINT_KIND = "logistic_regression"
_CHECKPOINT_VERSION = 1

# Stable categorical value lists so the feature encoding is the same
# for every advisor (DRY). Anything outside these sets folds to a
# trailing "unknown" bucket.
_STATE_VALUES = ("active", "archived", "stale", "unknown")
_PROVENANCE_VALUES = ("agent-created", "bundled", "hub", "unknown")


def _feature_names() -> list[str]:
    """Return the fixed feature order. Used by encoder + checkpoint."""
    out: list[str] = []
    out.extend(f"state_{s}" for s in _STATE_VALUES)
    out.extend(f"provenance_{p}" for p in _PROVENANCE_VALUES)
    out.append("pinned")
    out.extend(["log1p_use_count", "log1p_view_count", "log1p_patch_count"])
    return out


_FEATURE_NAMES: tuple[str, ...] = tuple(_feature_names())


def _encode(features: SkillFeatures) -> list[float]:
    """Project a :class:`SkillFeatures` into the fixed feature vector."""
    vec: list[float] = []
    state = features.state if features.state in _STATE_VALUES else "unknown"
    for s in _STATE_VALUES:
        vec.append(1.0 if state == s else 0.0)
    prov = features.provenance if features.provenance in _PROVENANCE_VALUES else "unknown"
    for p in _PROVENANCE_VALUES:
        vec.append(1.0 if prov == p else 0.0)
    vec.append(1.0 if features.pinned else 0.0)
    vec.append(math.log1p(max(features.use_count, 0)))
    vec.append(math.log1p(max(features.view_count, 0)))
    vec.append(math.log1p(max(features.patch_count, 0)))
    return vec


@dataclass(frozen=True, slots=True)
class LogisticRegressionAdvisor:
    """Trained multinomial logistic regression advisor.

    Frozen value type: same shape semantics as
    :class:`~autocontext.hermes.advisor.BaselineAdvisor` (a learned
    predictor as data, not a stateful object). Implements the
    :class:`Advisor` Protocol via ``predict``; also exposes
    ``predict_proba`` for calibration-aware downstream consumers.
    """

    labels: tuple[str, ...]
    feature_names: tuple[str, ...]
    weights: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]
    trained_on: int = 0
    seed: int = 0
    epochs: int = 0
    learning_rate: float = 0.0
    label_counts: dict[str, int] = field(default_factory=dict)

    def predict(self, features: SkillFeatures) -> str:
        probs = self.predict_proba(features)
        # Deterministic argmax: highest probability, ties broken by
        # CANONICAL_LABELS order.
        canonical = {label: i for i, label in enumerate(CANONICAL_LABELS)}
        best = min(probs.items(), key=lambda kv: (-kv[1], canonical.get(kv[0], len(CANONICAL_LABELS))))
        return best[0]

    def predict_proba(self, features: SkillFeatures) -> dict[str, float]:
        vec = _encode(features)
        scores = [
            sum(w * x for w, x in zip(row, vec, strict=True)) + b for row, b in zip(self.weights, self.intercepts, strict=True)
        ]
        # Softmax with max-subtraction for numerical stability.
        m = max(scores)
        exps = [math.exp(s - m) for s in scores]
        total = sum(exps)
        if total == 0.0:
            # Degenerate: all -inf logits. Fall back to uniform.
            uniform = 1.0 / max(len(self.labels), 1)
            return dict.fromkeys(self.labels, uniform)
        return {label: e / total for label, e in zip(self.labels, exps, strict=True)}


# --- Training -------------------------------------------------------------


def train_logistic(
    examples: list[CuratorDecisionExample],
    *,
    epochs: int = 200,
    learning_rate: float = 0.5,
    l2: float = 0.001,
    seed: int = 0,
) -> LogisticRegressionAdvisor:
    """Train a multinomial logistic regression on ``examples``.

    Pure-Python gradient descent on softmax cross-entropy with a
    small L2 penalty. Deterministic for the same ``seed`` (no batch
    shuffling — the dataset is small enough that full-batch GD is
    fine).

    Raises :class:`ValueError` when ``examples`` is empty.
    """
    if not examples:
        raise ValueError("no labeled examples; cannot train a logistic-regression advisor")

    # Stable label order: every label observed, ordered first by
    # CANONICAL_LABELS, then alphabetically for any extras. Keeps
    # checkpoint shape stable across runs.
    observed = sorted(
        {ex.label for ex in examples},
        key=lambda lbl: (CANONICAL_LABELS.index(lbl) if lbl in CANONICAL_LABELS else len(CANONICAL_LABELS), lbl),
    )
    labels = tuple(observed)
    label_index = {label: i for i, label in enumerate(labels)}

    encoded = [_encode(ex.features) for ex in examples]
    target = [label_index[ex.label] for ex in examples]
    n_features = len(_FEATURE_NAMES)
    n_labels = len(labels)
    n = len(examples)

    rng = random.Random(seed)
    # Tiny random init so symmetric labels don't share gradients exactly.
    weights = [[rng.uniform(-0.01, 0.01) for _ in range(n_features)] for _ in range(n_labels)]
    intercepts = [0.0 for _ in range(n_labels)]

    for _ in range(epochs):
        # Per-batch (full dataset) gradients.
        grad_w = [[0.0] * n_features for _ in range(n_labels)]
        grad_b = [0.0] * n_labels
        for vec, y in zip(encoded, target, strict=True):
            # Softmax over scores.
            scores = [sum(w * x for w, x in zip(weights[k], vec, strict=True)) + intercepts[k] for k in range(n_labels)]
            m_max = max(scores)
            exps = [math.exp(s - m_max) for s in scores]
            total = sum(exps)
            probs = [e / total for e in exps]
            # Gradient: (probs - one_hot(y)) ⊗ vec for the weight matrix,
            # (probs - one_hot(y)) for the intercept vector.
            for k in range(n_labels):
                err = probs[k] - (1.0 if k == y else 0.0)
                grad_b[k] += err
                row = grad_w[k]
                for j, xj in enumerate(vec):
                    row[j] += err * xj
        # Apply L2 + scale + step.
        for k in range(n_labels):
            for j in range(n_features):
                weights[k][j] -= learning_rate * (grad_w[k][j] / n + l2 * weights[k][j])
            intercepts[k] -= learning_rate * (grad_b[k] / n)

    label_counts: dict[str, int] = {}
    for ex in examples:
        label_counts[ex.label] = label_counts.get(ex.label, 0) + 1

    return LogisticRegressionAdvisor(
        labels=labels,
        feature_names=_FEATURE_NAMES,
        weights=tuple(tuple(row) for row in weights),
        intercepts=tuple(intercepts),
        trained_on=n,
        seed=seed,
        epochs=epochs,
        learning_rate=learning_rate,
        label_counts=label_counts,
    )


# --- Checkpoint round-trip ------------------------------------------------


def save_advisor(advisor: LogisticRegressionAdvisor, path: Path) -> None:
    """Persist ``advisor`` to JSON. Schema is the AC-708 slice 2a
    checkpoint contract (kind, version, labels, feature_names,
    weights, intercepts, trained_on, seed, epochs, learning_rate)."""
    payload: dict[str, Any] = {
        "kind": _CHECKPOINT_KIND,
        "version": _CHECKPOINT_VERSION,
        "labels": list(advisor.labels),
        "feature_names": list(advisor.feature_names),
        "weights": [list(row) for row in advisor.weights],
        "intercepts": list(advisor.intercepts),
        "trained_on": advisor.trained_on,
        "seed": advisor.seed,
        "epochs": advisor.epochs,
        "learning_rate": advisor.learning_rate,
        "label_counts": dict(advisor.label_counts),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_advisor(path: Path) -> LogisticRegressionAdvisor:
    """Load a checkpoint produced by :func:`save_advisor`.

    Raises :class:`FileNotFoundError` for missing files,
    :class:`ValueError` for invalid JSON or an unrecognized
    ``kind``. Future MLX / CUDA backends should declare their own
    ``kind`` value so this loader fails fast rather than
    silently treating a foreign checkpoint as logistic regression.
    """
    if not path.exists():
        raise FileNotFoundError(f"advisor checkpoint not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"invalid advisor checkpoint at {path}: {err.msg}") from err
    kind = payload.get("kind")
    if kind != _CHECKPOINT_KIND:
        raise ValueError(f"unknown advisor kind {kind!r} at {path}; expected {_CHECKPOINT_KIND!r}")
    labels = tuple(payload["labels"])
    feature_names = tuple(payload["feature_names"])
    weights = tuple(tuple(row) for row in payload["weights"])
    intercepts = tuple(payload["intercepts"])
    # PR #980 review (P2): validate the per-class / per-feature
    # dimensions before constructing the advisor. Mismatched shapes
    # would otherwise crash later inside `predict_proba` (zip on
    # weights[k] vs feature vector) with a confusing error far from
    # the malformed file.
    if not (len(labels) == len(weights) == len(intercepts)):
        raise ValueError(
            f"invalid advisor checkpoint at {path}: "
            f"labels={len(labels)}, weights={len(weights)}, intercepts={len(intercepts)} "
            "must all agree on the number of classes"
        )
    if any(len(row) != len(feature_names) for row in weights):
        bad = next((len(row) for row in weights if len(row) != len(feature_names)), -1)
        raise ValueError(
            f"invalid advisor checkpoint at {path}: feature_names has {len(feature_names)} entries "
            f"but a weights row has {bad} entries"
        )
    return LogisticRegressionAdvisor(
        labels=labels,
        feature_names=feature_names,
        weights=weights,
        intercepts=intercepts,
        trained_on=int(payload.get("trained_on", 0)),
        seed=int(payload.get("seed", 0)),
        epochs=int(payload.get("epochs", 0)),
        learning_rate=float(payload.get("learning_rate", 0.0)),
        label_counts=dict(payload.get("label_counts", {})),
    )


__all__ = [
    "LogisticRegressionAdvisor",
    "load_advisor",
    "save_advisor",
    "train_logistic",
]
