"""Validation subsystem — staged candidate validation and strategy checking."""
from __future__ import annotations

from autocontext.harness.validation.staged import (
    StageResult,
    StageStatus,
    ValidationPipeline,
    ValidationStage,
)

__all__ = [
    "StageResult",
    "StageStatus",
    "ValidationPipeline",
    "ValidationStage",
]
