"""the training-backend seam: availability selection and run indirection.

mlxlm is the only backend wired here, and it is the only one that trains a
LoRA-SFT adapter over the curated data_path file the ambient curate stage
writes. It requires Darwin plus the mlx runtime, so on a box without them
select_backend returns None and the train stage cleanly no-ops (no error). A
Linux or CUDA SFT-over-dataset backend is future work: until it lands, ambient
training only produces candidates on an mlx-capable box.

(The trl backend is deliberately not wired: it does on-policy
distillation/RLVR, generating its own prompts from the live scenario, so it
cannot consume a curated per-target dataset file.)

The ambient train stage talks only to this module, never to the heavy mlx
backend directly, so a cpu-only ci monkeypatches _availability and the
_BACKEND registry to exercise the whole stage without a real fine-tune.
gpu_hours is derived from the wall-clock training_seconds the backend reports,
which is what the usage ledger and budget floor consume.

Each backend in the registry carries its own arg-adapter mapping a TrainRequest
to that backend's exact kwargs, so a second backend with a different signature
can never be handed foreign kwargs: run_training dispatches through the adapter
rather than a fixed kwarg set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.mlxlm_backend import run_mlxlm_training
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


# preference order per charter method; first available wins
_METHOD_BACKENDS: dict[str, tuple[str, ...]] = {"sft-distill": ("mlxlm",)}
_BACKEND: dict[str, BackendEntry] = {
    "mlxlm": BackendEntry(fn=run_mlxlm_training, adapt=_adapt_mlxlm),
}


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
