"""the training-backend seam: availability selection and run indirection.

Two backends are wired, both training a LoRA-SFT adapter over the curated
data_path file the ambient curate stage writes: mlxlm (Darwin + mlx runtime)
and sft (cross-platform trl + torch, the linux/cuda path). For the sft-distill
method mlxlm is preferred and sft is the fallback, so select_backend returns
mlxlm where the mlx runtime is present and sft where only trl + torch are; on a
box with neither it returns None and the train stage cleanly no-ops (no error).

(The trl backend is deliberately not wired: it does on-policy
distillation/RLVR, generating its own prompts from the live scenario, so it
cannot consume a curated per-target dataset file.)

The ambient train stage talks only to this module, never to the heavy backend
implementations directly, so a cpu-only ci monkeypatches _availability and the
_BACKEND registry to exercise the whole stage without a real fine-tune.
gpu_hours is derived from the wall-clock training_seconds the backend reports,
which is what the usage ledger and budget floor consume.

Each backend in the registry carries its own arg-adapter mapping a TrainRequest
to that backend's exact kwargs, so a second backend with a different signature
can never be handed foreign kwargs: run_training dispatches through the adapter
rather than a fixed kwarg set. The sft backend additionally enforces a real
in-run wall-clock deadline (_DEADLINE_CAPABLE), which the train stage reads to
reserve an exact budget ceiling rather than a conservative envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.mlxlm_backend import run_mlxlm_training
from autocontext.training.autoresearch.sft_backend import run_sft_training
from autocontext.training.backends import default_backend_registry


@dataclass(slots=True)
class TrainRequest:
    scenario: str
    data_path: Path
    output_dir: Path
    base_model: str
    time_budget_seconds: int
    memory_limit_mb: int


@dataclass(slots=True)
class BackendEntry:
    """a registry record pairing a backend fn with its request->kwargs adapter."""

    fn: Callable[..., dict[str, float]]
    adapt: Callable[[TrainRequest], dict[str, Any]]


def _adapt_mlxlm(request: TrainRequest) -> dict[str, Any]:
    return {
        "scenario_name": request.scenario,
        "data_path": request.data_path,
        "output_dir": request.output_dir,
        "time_budget": request.time_budget_seconds,
        "memory_limit_mb": request.memory_limit_mb,
        "base_model": request.base_model,
        # dedupe is enabled so the curate crash-window duplicate rows are removed at
        # train time, honoring the mitigation curate.py documents.
        "dedupe": True,
    }


def _adapt_sft(request: TrainRequest) -> dict[str, Any]:
    return {
        "scenario_name": request.scenario,
        "data_path": request.data_path,
        "output_dir": request.output_dir,
        "base_model": request.base_model,
        "time_budget": request.time_budget_seconds,
        "memory_limit_mb": request.memory_limit_mb,
        # the whole-run wall-clock ceiling: run_sft_training's DeadlineCallback stops the run at
        # this many seconds, so the pre-flight can reserve time_budget_seconds exactly (no assess
        # envelope). run_sft_training's deadline_seconds is in SECONDS, matching time_budget_seconds.
        "deadline_seconds": float(request.time_budget_seconds),
    }


# preference order per charter method; first available wins. mlxlm is preferred on mac; sft is the
# linux/cuda fallback (select_backend returns the first AVAILABLE, so mlxlm wins where installed).
_METHOD_BACKENDS: dict[str, tuple[str, ...]] = {"sft-distill": ("mlxlm", "sft")}
_BACKEND: dict[str, BackendEntry] = {
    "mlxlm": BackendEntry(fn=run_mlxlm_training, adapt=_adapt_mlxlm),
    "sft": BackendEntry(fn=run_sft_training, adapt=_adapt_sft),
}
# backends that enforce a real in-run wall-clock deadline: their whole-run wall clock cannot exceed
# time_budget_seconds, so the train stage reserves the true ceiling for them instead of the
# conservative assess envelope. mlxlm has no in-run deadline, so it is deliberately absent.
_DEADLINE_CAPABLE: frozenset[str] = frozenset({"sft"})


def is_deadline_capable(backend_name: str) -> bool:
    """True when this backend enforces a real in-run wall-clock deadline.

    The train stage reads this to decide the budget reservation: a deadline-capable backend passes
    a deadline through its adapter (see the ``deadline_seconds`` invariant test), so its training
    compute cannot exceed time_budget_seconds and the exact ceiling is reserved. A backend without
    an in-run deadline (mlxlm) is reserved a conservative assess envelope instead.
    """
    return backend_name in _DEADLINE_CAPABLE


@dataclass(slots=True)
class TrainOutcome:
    checkpoint_path: Path
    backend: str
    metrics: dict[str, float]
    gpu_hours: float


def _availability() -> dict[str, bool]:
    registry = default_backend_registry()
    out: dict[str, bool] = {}
    for name in _BACKEND:
        backend = registry.get(name)
        out[name] = bool(backend and backend.is_available())
    return out


def select_backend(method: str) -> str | None:
    available = _availability()
    for name in _METHOD_BACKENDS.get(method, ()):  # unknown method -> no backend
        if available.get(name):
            return name
    return None


def run_training(backend_name: str, request: TrainRequest) -> TrainOutcome:
    entry = _BACKEND[backend_name]
    metrics = entry.fn(**entry.adapt(request))
    gpu_hours = float(metrics.get("training_seconds", 0.0)) / 3600.0
    checkpoint = request.output_dir / "adapters"
    return TrainOutcome(checkpoint_path=checkpoint, backend=backend_name, metrics=metrics, gpu_hours=gpu_hours)
