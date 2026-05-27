"""AC-708 slice 2c: CUDA-trained logistic-regression curator advisor.

Same model architecture as slices 2a/2b (multinomial logistic
regression on the fixed feature encoder from
:mod:`autocontext.hermes.trained_advisor`) but trained via PyTorch
gradient descent so the matrix multiplies can run on an NVIDIA GPU
when ``torch.cuda.is_available()``. The same code path runs on
CPU torch (just slower); the kind discriminator names the backend,
not the device.

The resulting checkpoint is the slice-2a JSON schema with
``kind: "cuda_logistic_regression"``. The loaded type is
:class:`~autocontext.hermes.trained_advisor.LogisticRegressionAdvisor`
since inference math is identical across backends.

torch is an optional dependency (``pip install autocontext[cuda]``).
:data:`HAS_CUDA_ADVISOR` reflects whether the import succeeded.
:func:`train_cuda_logistic` raises a clear :class:`RuntimeError`
when called without torch installed rather than crashing inside an
opaque ImportError.

Checkpoint format (JSON):

    {
      "kind": "cuda_logistic_regression",
      "version": 1,
      "labels": [...],
      "feature_names": [...],
      "weights": [[...], ...],
      "intercepts": [..., ..., ...],
      "trained_on": <int>,
      "seed": <int>,
      "epochs": <int>,
      "learning_rate": <float>,
      "backend": "cuda",
      "device": "cuda" | "cpu",
    }

The ``device`` field records whether the training run actually
landed on a CUDA device or fell back to CPU torch, for audit. The
kind stays ``cuda_logistic_regression`` either way because the
backend (PyTorch) is what differs from slice 2b's MLX.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.hermes.advisor import CANONICAL_LABELS, CuratorDecisionExample
from autocontext.hermes.trained_advisor import (
    _FEATURE_NAMES,
    LogisticRegressionAdvisor,
    _encode,
)

try:
    import importlib.util as _imp_util

    HAS_CUDA_ADVISOR = _imp_util.find_spec("torch") is not None
except ImportError:
    HAS_CUDA_ADVISOR = False

CUDA_CHECKPOINT_KIND = "cuda_logistic_regression"
_CHECKPOINT_VERSION = 1


def _require_torch() -> None:
    """Raise a clear error when the CUDA backend is requested without torch."""
    if not HAS_CUDA_ADVISOR:
        raise RuntimeError(
            "PyTorch is not installed; install autocontext with the `cuda` extra "
            "(e.g. `uv pip install autocontext[cuda]`) to use the CUDA advisor backend"
        )


def train_cuda_logistic(
    examples: list[CuratorDecisionExample],
    *,
    epochs: int = 200,
    learning_rate: float = 0.5,
    l2: float = 0.001,
    seed: int = 0,
) -> tuple[LogisticRegressionAdvisor, str]:
    """Train a multinomial logistic regression via PyTorch gradient descent.

    Same algorithm and hyperparameters as
    :func:`~autocontext.hermes.trained_advisor.train_logistic` and
    :func:`~autocontext.hermes.mlx_trained_advisor.train_mlx_logistic`,
    but the inner loop runs on PyTorch tensors so the matrix multiplies
    can be GPU-accelerated when ``torch.cuda.is_available()``. Falls
    back transparently to CPU torch when CUDA is not available.

    Returns a tuple ``(advisor, device)`` where ``advisor`` is the
    trained :class:`~autocontext.hermes.trained_advisor.LogisticRegressionAdvisor`
    (the same dataclass slice 2a / 2b return, so the loaded
    checkpoint type stays uniform across backends) and ``device`` is
    ``"cuda"`` or ``"cpu"`` reflecting where the training actually
    ran. PR #996 review (P2): the device must come from training,
    not from ``torch.cuda.is_available()`` at save time, otherwise
    a checkpoint trained on CUDA could be saved later from a
    CPU-only host (or vice versa) and the audit record would lie.

    Raises :class:`ValueError` when ``examples`` is empty;
    :class:`RuntimeError` when torch is not installed.
    """
    _require_torch()
    if not examples:
        raise ValueError("no labeled examples; cannot train a CUDA logistic-regression advisor")

    import torch

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    observed = sorted(
        {ex.label for ex in examples},
        key=lambda lbl: (
            CANONICAL_LABELS.index(lbl) if lbl in CANONICAL_LABELS else len(CANONICAL_LABELS),
            lbl,
        ),
    )
    labels = tuple(observed)
    label_index = {label: i for i, label in enumerate(labels)}

    encoded = [_encode(ex.features) for ex in examples]
    target = [label_index[ex.label] for ex in examples]
    n_features = len(_FEATURE_NAMES)
    n_labels = len(labels)
    n = len(examples)

    # Deterministic init across runs.
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    X = torch.tensor(encoded, dtype=torch.float32, device=device)
    y = torch.tensor(target, dtype=torch.long, device=device)

    # Tight uniform init matches the slice-2a scale; .empty_().uniform_()
    # keeps things deterministic via the seeded RNG above.
    weights = torch.empty(n_labels, n_features, device=device).uniform_(-0.01, 0.01)
    intercepts = torch.zeros(n_labels, device=device)
    weights.requires_grad_(True)
    intercepts.requires_grad_(True)

    optimizer = torch.optim.SGD([weights, intercepts], lr=learning_rate)
    loss_fn = torch.nn.CrossEntropyLoss(reduction="mean")

    for _ in range(epochs):
        optimizer.zero_grad()
        logits = X @ weights.t() + intercepts
        loss = loss_fn(logits, y) + l2 * (weights * weights).sum()
        loss.backward()
        optimizer.step()

    # Materialize back to Python floats so the resulting dataclass is
    # backend-agnostic at inference time (matches slice 2a's predict
    # path; no torch needed to load + serve the checkpoint).
    weights_py: tuple[tuple[float, ...], ...] = tuple(tuple(float(v) for v in row) for row in weights.detach().cpu().tolist())
    intercepts_py: tuple[float, ...] = tuple(float(v) for v in intercepts.detach().cpu().tolist())

    label_counts: dict[str, int] = {}
    for ex in examples:
        label_counts[ex.label] = label_counts.get(ex.label, 0) + 1

    advisor = LogisticRegressionAdvisor(
        labels=labels,
        feature_names=_FEATURE_NAMES,
        weights=weights_py,
        intercepts=intercepts_py,
        trained_on=n,
        seed=seed,
        epochs=epochs,
        learning_rate=learning_rate,
        label_counts=label_counts,
    )
    return advisor, device.type


def save_cuda_advisor(
    advisor: LogisticRegressionAdvisor,
    path: Path,
    *,
    device: str,
) -> None:
    """Persist ``advisor`` to JSON with the CUDA kind discriminator.

    Same JSON schema as
    :func:`~autocontext.hermes.trained_advisor.save_advisor` modulo the
    ``kind`` field, an additional ``backend`` audit field, and a
    ``device`` field that records where training actually ran (the
    second element of the tuple returned by :func:`train_cuda_logistic`).

    PR #996 review (P2): the ``device`` argument is required so the
    audit record reflects the host that trained the model, not
    whatever device happens to be available at save time on a
    different host.
    """
    if device not in {"cuda", "cpu"}:
        raise ValueError(f"unexpected training device {device!r}; expected 'cuda' or 'cpu'")
    payload: dict[str, Any] = {
        "kind": CUDA_CHECKPOINT_KIND,
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
        "backend": "cuda",
        "device": device,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "CUDA_CHECKPOINT_KIND",
    "HAS_CUDA_ADVISOR",
    "save_cuda_advisor",
    "train_cuda_logistic",
]
