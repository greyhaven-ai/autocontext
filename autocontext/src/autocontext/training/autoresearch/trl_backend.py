"""Cross-platform TRL backend: on-policy distillation (GKD) and RLVR (GRPO).

The MLX backends (`opd`, `grpo`) are Apple-Silicon only. This backend is the
cross-platform (Linux / NVIDIA / CPU) counterpart, wrapping HuggingFace TRL's validated
trainers so the same two methods run at scale where a real validation run happens:

- ``mode="gkd"`` -> on-policy distillation via TRL ``GKDTrainer`` (the off-the-shelf
  equivalent of the MLX `opd` backend; ``lmbda`` = on-policy fraction, ``beta`` = forward
  <-> reverse KL, ``beta=1.0`` = reverse KL).
- ``mode="grpo"`` -> RLVR via TRL ``GRPOTrainer``, reusing the SAME tested
  :func:`score_completions` reward adapter as the MLX `grpo` backend.

TRL does all the numerics, so this module has no heavy import at module scope (only the
pure config / dataset / reward seams). The trainer-instantiating runner imports
``trl`` / ``torch`` / ``peft`` lazily, so the module + its tests import without them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.grpo_backend import build_prompt_rows, score_completions

# Cross-platform HF model ids (not the MLX 4-bit community repos). Teacher/student must
# share a tokenizer; defaults are the same Qwen2.5 family.
DEFAULT_STUDENT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
DEFAULT_TEACHER_MODEL = "Qwen/Qwen2.5-7B-Instruct"
SUPPORTED_MODES = ("gkd", "grpo")
# PEFT LoRA config passed to the TRL trainers (kwargs for peft.LoraConfig).
_LORA = {"r": 8, "lora_alpha": 16, "lora_dropout": 0.0, "task_type": "CAUSAL_LM"}

__all__ = [
    "DEFAULT_STUDENT_MODEL",
    "DEFAULT_TEACHER_MODEL",
    "SUPPORTED_MODES",
    "build_chat_dataset_rows",
    "build_gkd_config_kwargs",
    "build_grpo_config_kwargs",
    "build_prompt_dataset_rows",
    "make_reward_func",
    "make_time_budget_callback",
    "run_trl_training",
]


def _import_gkd() -> tuple[Any, Any]:
    """Resolve ``(GKDConfig, GKDTrainer)`` across TRL layouts.

    Recent TRL moved GKD under ``trl.experimental.gkd``; older releases expose it at the
    top level. Try the experimental path first, fall back to the top level, so the GKD arm
    works regardless of the resolved TRL version instead of failing on import mid-run.
    """
    try:
        from trl.experimental.gkd import GKDConfig, GKDTrainer
    except ImportError:
        from trl import GKDConfig, GKDTrainer
    return GKDConfig, GKDTrainer


def build_gkd_config_kwargs(
    *,
    output_dir: str,
    teacher_model: str,
    learning_rate: float = 1e-5,
    lmbda: float = 1.0,
    beta: float = 1.0,
    temperature: float = 0.9,
    max_new_tokens: int = 256,
    num_train_epochs: float = 1.0,
    max_steps: int = -1,
    per_device_train_batch_size: int = 1,
) -> dict[str, Any]:
    """Kwargs for ``trl.experimental.gkd.GKDConfig`` (on-policy distillation).

    Defaults encode the on-policy distillation recipe: ``lmbda=1.0`` (fully on-policy
    student rollouts) and ``beta=1.0`` (reverse KL, mode-seeking and unhackable).
    ``max_steps`` (>0 caps total optimizer steps, overriding epochs) and
    ``per_device_train_batch_size`` are threaded from the generic train controls.
    """
    return {
        "output_dir": output_dir,
        "teacher_model_name_or_path": teacher_model,
        "learning_rate": learning_rate,
        "lmbda": lmbda,
        "beta": beta,
        "temperature": temperature,
        "max_new_tokens": max_new_tokens,
        "num_train_epochs": num_train_epochs,
        "max_steps": max_steps,
        "per_device_train_batch_size": per_device_train_batch_size,
    }


def build_grpo_config_kwargs(
    *,
    output_dir: str,
    learning_rate: float = 1e-5,
    num_generations: int = 8,
    max_completion_length: int = 256,
    max_prompt_length: int = 512,
    beta: float = 0.0,
    temperature: float = 1.0,
    num_train_epochs: float = 1.0,
    max_steps: int = -1,
    per_device_train_batch_size: int = 8,
) -> dict[str, Any]:
    """Kwargs for ``trl.GRPOConfig`` (RLVR). ``beta=0.0`` follows TRL's KL-free default.

    ``max_steps`` (>0 caps total optimizer steps) and ``per_device_train_batch_size`` are
    threaded from the generic train controls.
    """
    return {
        "output_dir": output_dir,
        "learning_rate": learning_rate,
        "num_generations": num_generations,
        "max_completion_length": max_completion_length,
        "max_prompt_length": max_prompt_length,
        "beta": beta,
        "temperature": temperature,
        "num_train_epochs": num_train_epochs,
        "max_steps": max_steps,
        "per_device_train_batch_size": per_device_train_batch_size,
    }


def build_chat_dataset_rows(scenario: Any, n_prompts: int) -> list[dict[str, Any]]:
    """GKD dataset rows: ``{"messages": [{"role": "user", "content": prompt}]}`` per prompt."""
    return [{"messages": [{"role": "user", "content": row["prompt"]}]} for row in build_prompt_rows(scenario, n_prompts)]


def build_prompt_dataset_rows(scenario: Any, n_prompts: int) -> list[dict[str, str]]:
    """GRPO dataset rows: ``{"prompt", "answer"}`` (TRL passes the extra ``answer`` column
    to the reward function as a kwarg). This is exactly what :func:`build_prompt_rows` emits."""
    return build_prompt_rows(scenario, n_prompts)


def make_reward_func(scenario: Any) -> Any:
    """A TRL GRPO reward function delegating to the tested :func:`score_completions`.

    TRL calls reward functions as ``reward(prompts=, completions=, <extra columns>=)`` and
    expects a list of floats; the ``answer`` dataset column arrives as ``answer=`` (a list
    aligned with the batch), which is the per-instance state the reward verifies against.
    """

    def _reward(prompts: Any = None, completions: Any = None, **kwargs: Any) -> list[float]:
        answers = kwargs.get("answer") or kwargs.get("answers")
        return score_completions(scenario, completions, answers=answers)

    return _reward


def make_time_budget_callback(time_budget: float) -> Any:
    """A ``transformers.TrainerCallback`` that stops training once ``time_budget`` (s) elapses.

    TRL/transformers loops run to ``num_train_epochs``/``max_steps`` with no wall-clock cap,
    so this enforces the budget at step boundaries (transformers imported lazily so the
    module stays importable without it)."""
    from transformers import TrainerCallback

    class _TimeBudgetCallback(TrainerCallback):  # type: ignore[misc, valid-type]
        def __init__(self, budget: float) -> None:
            self.budget = budget
            self.started = time.monotonic()

        def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
            if (time.monotonic() - self.started) >= self.budget:
                control.should_training_stop = True
            return control

    return _TimeBudgetCallback(time_budget)


def run_trl_training(
    *,
    mode: str = "gkd",
    scenario_name: str,
    output_dir: Path,
    student_model: str = "",
    teacher_model: str = "",
    n_prompts: int = 64,
    learning_rate: float = 1e-5,
    max_steps: int = -1,
    batch_size: int = 0,
    register_import: str | None = None,
    time_budget: int = 3600,
    memory_limit_mb: int = 16384,  # noqa: ARG001 - backend-signature parity
) -> dict[str, float]:
    """Run TRL ``gkd`` (on-policy distillation) or ``grpo`` (RLVR) and return summary metrics.

    Cross-platform (needs ``trl`` + ``torch`` + ``peft``); imports them lazily. ``student_model``
    / ``teacher_model`` empty fall back to same-family Qwen2.5 defaults; a tokenizer mismatch
    is rejected by :func:`assert_vocab_compatible`. ``max_steps`` (>0 caps optimizer steps) and
    ``batch_size`` (>0 sets per-device batch) are threaded from the generic train controls.
    ``register_import`` registers a consumer-repo scenario in this process before lookup.
    """
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"trl mode must be one of {SUPPORTED_MODES}, got {mode!r}")

    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.distill_common import assert_vocab_compatible, build_trl_metrics
    from autocontext.training.autoresearch.train import _peak_memory_mb, _preflight_backend_deps

    _preflight_backend_deps("trl")
    student_model = student_model or DEFAULT_STUDENT_MODEL
    teacher_model = teacher_model or DEFAULT_TEACHER_MODEL
    if register_import:
        exec(register_import, {})  # noqa: S102 - trusted operator-supplied registration hook
    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")
    scenario = SCENARIO_REGISTRY[scenario_name]()

    output_dir.mkdir(parents=True, exist_ok=True)
    peft_config = LoraConfig(**_LORA)
    tokenizer = AutoTokenizer.from_pretrained(student_model)
    started = time.monotonic()
    callbacks = [make_time_budget_callback(float(time_budget))]

    if mode == "gkd":
        gkd_config_cls, gkd_trainer_cls = _import_gkd()

        teacher_tok = AutoTokenizer.from_pretrained(teacher_model)
        s_vocab, t_vocab = getattr(tokenizer, "vocab_size", None), getattr(teacher_tok, "vocab_size", None)
        if isinstance(s_vocab, int) and isinstance(t_vocab, int):
            assert_vocab_compatible(s_vocab, t_vocab)
        dataset = Dataset.from_list(build_chat_dataset_rows(scenario, n_prompts))
        gkd_kwargs: dict[str, Any] = dict(
            output_dir=str(output_dir), teacher_model=teacher_model, learning_rate=learning_rate, max_steps=max_steps
        )
        if batch_size > 0:
            gkd_kwargs["per_device_train_batch_size"] = batch_size
        args = gkd_config_cls(**build_gkd_config_kwargs(**gkd_kwargs))
        trainer = gkd_trainer_cls(
            model=AutoModelForCausalLM.from_pretrained(student_model),
            teacher_model=AutoModelForCausalLM.from_pretrained(teacher_model),
            args=args,
            processing_class=tokenizer,
            train_dataset=dataset,
            peft_config=peft_config,
            callbacks=callbacks,
        )
    else:  # grpo
        from trl import GRPOConfig, GRPOTrainer

        dataset = Dataset.from_list(build_prompt_dataset_rows(scenario, n_prompts))
        grpo_kwargs: dict[str, Any] = dict(output_dir=str(output_dir), learning_rate=learning_rate, max_steps=max_steps)
        if batch_size > 0:
            grpo_kwargs["per_device_train_batch_size"] = batch_size
        args = GRPOConfig(**build_grpo_config_kwargs(**grpo_kwargs))
        trainer = GRPOTrainer(
            model=student_model,
            args=args,
            reward_funcs=make_reward_func(scenario),
            train_dataset=dataset,
            processing_class=tokenizer,
            peft_config=peft_config,
            callbacks=callbacks,
        )

    train_output = trainer.train()
    trainer.save_model(str(output_dir))
    training_loss = float(getattr(train_output, "training_loss", float("nan")))
    num_steps = float(getattr(getattr(trainer, "state", None), "global_step", 0) or 0)
    # build_trl_metrics gives a FINITE avg_score (negative training loss) so the training
    # runner's keep/discard comparison selects this checkpoint instead of discarding on NaN.
    return build_trl_metrics(
        training_loss=training_loss,
        num_steps=num_steps,
        training_seconds=time.monotonic() - started,
        peak_memory_mb=_peak_memory_mb(),
    )
