"""Default base/student/teacher model ids for the adapter training backends.

Kept in a dependency-free module (no ``mlx`` / ``torch`` imports) so the lightweight
``backends.py`` can report a backend's *effective* default base model without importing the
heavy backend implementation. This is the single source of truth: the autoresearch backend
modules re-export these, so the model the runner records on a published adapter is exactly the
one the training subprocess trains against (a mismatch would serve the adapter on the wrong base).
"""

from __future__ import annotations

# mlx-lm LoRA SFT (mlxlm backend).
MLXLM_DEFAULT_BASE_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"

# mlx-lm-lora RLVR (grpo backend).
GRPO_DEFAULT_BASE_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"

# mlx-lm on-policy distillation (opd backend): student is the trained model, teacher is frozen.
OPD_DEFAULT_STUDENT_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
OPD_DEFAULT_TEACHER_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
