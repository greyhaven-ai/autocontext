"""R1-style training pipeline: distillation cold-start -> RLVR.

Runs the two-stage recipe end-to-end as one capability:

  1. DISTILL  -- LoRA-finetune the base on (reasoning) records via the mlx-lm backend,
                 producing a cold-start adapter.
  2. RLVR     -- GRPO/GSPO from the scenario verifier, RESUMING that adapter (so RL
                 builds on the cold-start instead of restarting from the base model).

This is the chaining that makes "distill -> RLVR" a first-class pipeline rather than two
disconnected backends: the distilled adapter is threaded into the RLVR stage's
``resume_adapter_file``. Stage functions are module-level so the orchestration is
unit-testable without MLX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The RLVR stage gates base capability (it needs a capable instruct base; weak bases hit a
# documented RLVR ceiling), and both stages MUST share one base so RLVR can resume the
# distilled adapter, so the pipeline default is the RLVR backend's default rather than the
# distillation backend's smaller one.
from autocontext.training.autoresearch.grpo_backend import DEFAULT_BASE_MODEL, run_grpo_training
from autocontext.training.autoresearch.mlxlm_backend import run_mlxlm_training


def distilled_adapter_path(output_dir: Any) -> Path:
    """Where the distillation stage writes its LoRA adapter (the RLVR cold-start)."""
    return Path(output_dir) / "distill" / "adapters" / "adapters.safetensors"


def _apply_register_import(register_import: str | None) -> None:
    """Execute a consumer's scenario-registration snippet IN THIS PROCESS.

    The distillation stage (``run_mlxlm_training``) runs in-process and looks the scenario
    up in ``SCENARIO_REGISTRY`` before reading data, so a consumer-only scenario must be
    registered in the parent here, not only inside the RLVR subprocess's reward module.
    """
    if register_import:
        exec(register_import, {})  # noqa: S102 - trusted operator-supplied registration hook


def run_r1_pipeline(
    *,
    scenario_name: str,
    data_path: Any,
    output_dir: Any,
    base_model: str = "",
    register_import: str | None = None,
    num_layers: int = 8,
    time_budget: int = 3600,
    memory_limit_mb: int = 16384,
    distill_kwargs: dict[str, Any] | None = None,
    rlvr_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run distillation cold-start then RLVR (resuming the cold-start adapter).

    ``register_import`` registers a consumer-repo scenario; it is applied both in this
    parent process (for the in-process distill stage) and inside the RLVR subprocess.
    Both stages share one ``base_model`` (default ``DEFAULT_BASE_MODEL``) so RLVR can
    resume the distilled adapter. ``distill_kwargs`` / ``rlvr_kwargs`` pass stage-specific
    options. Returns ``{"distill", "rlvr"}`` plus the headline (final RLVR)
    ``avg_score`` / ``valid_rate`` and the resumed adapter path.
    """
    base = Path(output_dir)
    base_model = base_model or DEFAULT_BASE_MODEL
    # Register a consumer scenario before the in-process distill stage queries the registry.
    _apply_register_import(register_import)

    distill_metrics = run_mlxlm_training(
        scenario_name=scenario_name,
        data_path=Path(data_path),
        output_dir=base / "distill",
        base_model=base_model,
        num_layers=num_layers,
        time_budget=time_budget,
        memory_limit_mb=memory_limit_mb,
        **(distill_kwargs or {}),
    )

    adapter = distilled_adapter_path(base)
    resume = str(adapter) if adapter.exists() else None

    rlvr_metrics = run_grpo_training(
        scenario_name=scenario_name,
        output_dir=base / "rlvr",
        base_model=base_model,
        resume_adapter_file=resume,
        register_import=register_import,
        num_layers=num_layers,
        time_budget=time_budget,
        memory_limit_mb=memory_limit_mb,
        **(rlvr_kwargs or {}),
    )

    return {
        "distill": distill_metrics,
        "rlvr": rlvr_metrics,
        "resume_adapter_file": resume,
        "avg_score": float(rlvr_metrics.get("avg_score", 0.0)),
        "valid_rate": float(rlvr_metrics.get("valid_rate", 0.0)),
    }
