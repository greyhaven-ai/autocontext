"""the training-backend seam: availability selection and run indirection.

The ambient train stage talks only to this module, never to the heavy mlx or
trl backends directly, so a cpu-only ci monkeypatches _availability and
_BACKEND_FNS to exercise the whole stage without a real fine-tune. gpu_hours
is derived from the wall-clock training_seconds the backend reports, which is
what the usage ledger and budget floor consume.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autocontext.training.autoresearch.mlxlm_backend import run_mlxlm_training
from autocontext.training.autoresearch.trl_backend import run_trl_training
from autocontext.training.backends import default_backend_registry

# preference order per charter method; first available wins
_METHOD_BACKENDS: dict[str, tuple[str, ...]] = {"sft-distill": ("mlxlm", "trl")}
_BACKEND_FNS: dict[str, Callable[..., dict[str, float]]] = {
    "mlxlm": run_mlxlm_training,
    "trl": run_trl_training,
}


@dataclass(slots=True)
class TrainRequest:
    scenario: str
    data_path: Path
    output_dir: Path
    base_model: str
    time_budget_seconds: int
    memory_limit_mb: int


@dataclass(slots=True)
class TrainOutcome:
    checkpoint_path: Path
    backend: str
    metrics: dict[str, float]
    gpu_hours: float


def _availability() -> dict[str, bool]:
    registry = default_backend_registry()
    out: dict[str, bool] = {}
    for name in _BACKEND_FNS:
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
    fn = _BACKEND_FNS[backend_name]
    metrics = fn(
        scenario_name=request.scenario,
        data_path=request.data_path,
        output_dir=request.output_dir,
        time_budget=request.time_budget_seconds,
        memory_limit_mb=request.memory_limit_mb,
        base_model=request.base_model,
    )
    gpu_hours = float(metrics.get("training_seconds", 0.0)) / 3600.0
    checkpoint = request.output_dir / "adapters"
    return TrainOutcome(checkpoint_path=checkpoint, backend=backend_name, metrics=metrics, gpu_hours=gpu_hours)
