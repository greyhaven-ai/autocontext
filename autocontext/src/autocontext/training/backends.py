"""Training backend abstraction for MLX and CUDA (AC-286).

Provides a clean backend interface so MLX and future CUDA training
can publish into the same model-selection layer. Each backend knows
its name, availability, default checkpoint paths, and metadata.

Key types:
- TrainingBackend: abstract interface
- MLXBackend: Apple Silicon MLX backend
- CUDABackend: NVIDIA CUDA backend (availability gated)
- BackendRegistry: registered backends by name
- default_backend_registry(): pre-populated with builtins
"""

from __future__ import annotations

import logging
import platform
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TrainingBackend(ABC):
    """Abstract interface for a training/distillation backend."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier: 'mlx', 'cuda', etc."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend can run on the current system."""

    @abstractmethod
    def default_checkpoint_dir(self, scenario: str) -> Path:
        """Default checkpoint directory for a scenario."""

    def metadata(self) -> dict[str, Any]:
        """Backend metadata for registry records."""
        return {
            "name": self.name,
            "available": self.is_available(),
            "runtime_types": self.supported_runtime_types(),
        }

    def supported_runtime_types(self) -> list[str]:
        """Runtime types this backend can serve."""
        return ["provider"]


class MLXBackend(TrainingBackend):
    """Apple Silicon MLX backend."""

    @property
    def name(self) -> str:
        return "mlx"

    def is_available(self) -> bool:
        if platform.system() != "Darwin":
            return False
        try:
            import importlib.util

            return importlib.util.find_spec("mlx") is not None
        except Exception:
            logger.debug("training.backends: caught Exception", exc_info=True)
            return False

    def default_checkpoint_dir(self, scenario: str) -> Path:
        return Path("models") / scenario / "mlx"

    def supported_runtime_types(self) -> list[str]:
        return ["provider", "pi"]


class CUDABackend(TrainingBackend):
    """NVIDIA CUDA backend."""

    @property
    def name(self) -> str:
        return "cuda"

    def is_available(self) -> bool:
        try:
            import importlib.util

            if importlib.util.find_spec("torch") is None:
                return False

            import importlib

            torch_module = importlib.import_module("torch")
            cuda_module = getattr(torch_module, "cuda", None)
            return bool(cuda_module is not None and cuda_module.is_available())
        except Exception:
            logger.debug("training.backends: caught Exception", exc_info=True)
            return False

    def default_checkpoint_dir(self, scenario: str) -> Path:
        return Path("models") / scenario / "cuda"

    def supported_runtime_types(self) -> list[str]:
        return ["provider"]


class BackendRegistry:
    """Registry of training backends by name."""

    def __init__(self) -> None:
        self._backends: dict[str, TrainingBackend] = {}

    def register(self, backend: TrainingBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> TrainingBackend | None:
        return self._backends.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._backends.keys())

    def list_all(self) -> list[TrainingBackend]:
        return list(self._backends.values())


def default_backend_registry() -> BackendRegistry:
    """Create a registry pre-populated with builtin backends."""
    registry = BackendRegistry()
    registry.register(MLXBackend())
    registry.register(CUDABackend())
    return registry
