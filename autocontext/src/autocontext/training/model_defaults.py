"""Default base/student/teacher model ids for the adapter training backends.

Kept in a dependency-free module (no ``mlx`` / ``torch`` imports) so the lightweight
``backends.py`` can report a backend's *effective* default base model without importing the
heavy backend implementation. This is the single source of truth: the autoresearch backend
modules re-export these, so the model the runner records on a published adapter is exactly the
one the training subprocess trains against (a mismatch would serve the adapter on the wrong base).
"""

from __future__ import annotations

from typing import Any

# mlx-lm LoRA SFT (mlxlm backend).
MLXLM_DEFAULT_BASE_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"

# mlx-lm-lora RLVR (grpo backend).
GRPO_DEFAULT_BASE_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"

# mlx-lm on-policy distillation (opd backend): student is the trained model, teacher is frozen.
OPD_DEFAULT_STUDENT_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
OPD_DEFAULT_TEACHER_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"

# Opt-in larger CUDA/TRL profiles. Defaults stay local/small; profiles make the larger
# student/base plan one flag instead of a pile of fragile knobs.
MODEL_SCALE_PROFILES: dict[str, dict[str, Any]] = {
    "cuda_qlora_7b_rlvr": {
        "backend": "trl",
        "trl_mode": "grpo",
        "base_model": "Qwen/Qwen2.5-7B-Instruct",
        "teacher_model": "Qwen/Qwen2.5-14B-Instruct",
        "base_model_parameter_count": 7_000_000_000,
        "base_model_quantization": "nf4",
        "memory_limit_mb": 24_576,
        "device_count": 1,
        "sharding_strategy": "none",
        "per_device_memory_limit_mb": 24_576,
        "deployment_target_vram_mb": 24_576,
    },
    "cuda_sharded_32b_distill": {
        "backend": "trl",
        "trl_mode": "gkd",
        "base_model": "Qwen/Qwen2.5-32B-Instruct",
        "teacher_model": "Qwen/Qwen2.5-72B-Instruct",
        "base_model_parameter_count": 32_000_000_000,
        "base_model_quantization": "nf4",
        "memory_limit_mb": 98_304,
        "device_count": 4,
        "sharding_strategy": "deepspeed_zero3",
        "per_device_memory_limit_mb": 24_576,
        "deployment_target_vram_mb": 24_576,
    },
}


def list_model_scale_profiles() -> list[str]:
    return sorted(MODEL_SCALE_PROFILES)


def get_model_scale_profile(name: str) -> dict[str, Any]:
    try:
        return dict(MODEL_SCALE_PROFILES[name])
    except KeyError as exc:
        raise ValueError(f"unknown model scale profile {name!r}; expected one of {list_model_scale_profiles()}") from exc
