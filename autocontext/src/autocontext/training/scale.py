"""Training scale metadata for larger-model pipeline planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_VALID_SHARDING = frozenset({"none", "fsdp", "deepspeed_zero3"})


@dataclass(frozen=True, slots=True)
class TrainingScaleProfile:
    """Hardware/model scale intent recorded with trained artifacts."""

    device_count: int = 1
    sharding_strategy: str = "none"
    memory_limit_mb: int = 16384
    per_device_memory_limit_mb: int = 0
    base_model_parameter_count: int = 0
    base_model_quantization: str = ""
    deployment_target_vram_mb: int = 0

    def __post_init__(self) -> None:
        if self.device_count < 1:
            raise ValueError("device_count must be >= 1")
        if self.sharding_strategy not in _VALID_SHARDING:
            raise ValueError(f"sharding_strategy must be one of {sorted(_VALID_SHARDING)}")
        for field_name in (
            "memory_limit_mb",
            "per_device_memory_limit_mb",
            "base_model_parameter_count",
            "deployment_target_vram_mb",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "device_count": self.device_count,
            "sharding_strategy": self.sharding_strategy,
            "memory_limit_mb": self.memory_limit_mb,
            "per_device_memory_limit_mb": self.per_device_memory_limit_mb or self.memory_limit_mb,
            "base_model_parameter_count": self.base_model_parameter_count,
            "base_model_quantization": self.base_model_quantization,
            "deployment_target_vram_mb": self.deployment_target_vram_mb,
        }


def training_scale_metadata_from_config(config: Any) -> dict[str, Any]:
    """Return registry-safe scale metadata from a training config object."""
    profile = TrainingScaleProfile(
        device_count=int(getattr(config, "device_count", 1)),
        sharding_strategy=str(getattr(config, "sharding_strategy", "none")),
        memory_limit_mb=int(getattr(config, "memory_limit_mb", 16384)),
        per_device_memory_limit_mb=int(getattr(config, "per_device_memory_limit_mb", 0)),
        base_model_parameter_count=int(getattr(config, "base_model_parameter_count", 0)),
        base_model_quantization=str(getattr(config, "base_model_quantization", "")),
        deployment_target_vram_mb=int(getattr(config, "deployment_target_vram_mb", 0)),
    )
    return profile.to_metadata()
