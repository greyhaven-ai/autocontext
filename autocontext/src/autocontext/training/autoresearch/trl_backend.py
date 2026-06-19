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
from importlib import import_module
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.grpo_backend import build_prompt_rows, score_completions

# Cross-platform HF model ids (not the MLX 4-bit community repos). Teacher/student must
# share a tokenizer; defaults are the same Qwen2.5 family.
DEFAULT_STUDENT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
# Teacher must share the student's LOGIT vocab dim for GKD: Qwen2.5 0.5B/1.5B/3B = 151936,
# but 7B+ = 152064, so 3B (not 7B) is the vocab-compatible teacher for the 1.5B student.
DEFAULT_TEACHER_MODEL = "Qwen/Qwen2.5-3B-Instruct"
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
    "collect_hf_token_pressure",
    "run_trl_training",
]


def _import_gkd() -> tuple[Any, Any]:
    """Resolve ``(GKDConfig, GKDTrainer)`` across TRL layouts.

    Recent TRL moved GKD under ``trl.experimental.gkd``; older releases expose it at the
    top level. Try the experimental path first, fall back to the top level, so the GKD arm
    works regardless of the resolved TRL version instead of failing on import mid-run.
    """
    try:
        from trl.experimental.gkd import GKDConfig, GKDTrainer  # type: ignore[import-not-found]
    except ImportError:
        from trl import GKDConfig, GKDTrainer  # type: ignore[import-not-found]
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
    seed: int = 0,
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
        "seed": seed,
    }


def build_grpo_config_kwargs(
    *,
    output_dir: str,
    learning_rate: float = 1e-5,
    num_generations: int = 8,
    # 512, not 256: reasoning tasks (e.g. GSM8K) need room for step-by-step work + the final
    # answer. At 256 every completion truncates before the answer, the verifier scores them all
    # 0, reward variance is 0, and GRPO gets no gradient -- RLVR silently learns nothing.
    max_completion_length: int = 512,
    # KL penalty toward the reference policy. TRL's default is 0.0 (no KL), which lets a
    # small-model / short-run RLVR job drift off the base distribution and OVERFIT the train
    # prompts -- observed directly: train reward climbed while held-out accuracy fell. 0.04
    # (the DeepSeekMath GRPO value) anchors the policy and is the safer default for the typical
    # autocontext loop; set 0.0 for the KL-free / R1-Zero-style regime at scale.
    beta: float = 0.04,
    temperature: float = 1.0,
    num_train_epochs: float = 1.0,
    max_steps: int = -1,
    per_device_train_batch_size: int = 8,
    seed: int = 0,
) -> dict[str, Any]:
    """Kwargs for ``trl.GRPOConfig`` (RLVR). ``beta`` is the reference-policy KL penalty.

    Only widely-stable GRPOConfig fields are passed (e.g. ``max_prompt_length`` is omitted
    -- some TRL versions reject it; the default prompt handling is fine). ``max_steps`` (>0
    caps total optimizer steps) and ``per_device_train_batch_size`` thread the generic controls.

    ``beta`` must be ``>= 0``: it is a KL *penalty*, so a negative value would reward the policy
    for drifting off the reference -- the opposite of regularization. The CLI guards this, but so
    does this lower-level entrypoint because harnesses call ``run_trl_training`` / ``run_training``
    (which route here) directly. ``beta == 0`` is the valid KL-free / R1-Zero-style opt-out.
    """
    if beta < 0:
        raise ValueError(f"GRPO beta (reference-policy KL penalty) must be >= 0; got {beta!r}")
    return {
        "output_dir": output_dir,
        "learning_rate": learning_rate,
        "num_generations": num_generations,
        "max_completion_length": max_completion_length,
        "beta": beta,
        "temperature": temperature,
        "num_train_epochs": num_train_epochs,
        "max_steps": max_steps,
        "per_device_train_batch_size": per_device_train_batch_size,
        "seed": seed,
    }


def build_chat_dataset_rows(scenario: Any, n_prompts: int) -> list[dict[str, Any]]:
    """GKD dataset rows: a user turn plus a placeholder assistant turn.

    TRL's ``DataCollatorForChatML`` splits each row into prompt (``messages[:-1]``) and a
    target turn (the last message), so a single user-only message makes ``messages[:-1]``
    empty and the collator raises ``IndexError`` building the prompt. On-policy GKD
    (``lmbda=1.0``) resamples the completion from the student, so the assistant content is a
    throwaway placeholder; it only has to give the collator a prompt/target boundary.
    """
    return [
        {
            "messages": [
                {"role": "user", "content": row["prompt"]},
                {"role": "assistant", "content": "(on-policy: resampled from the student)"},
            ]
        }
        for row in build_prompt_rows(scenario, n_prompts)
    ]


def build_prompt_dataset_rows(scenario: Any, n_prompts: int) -> list[dict[str, Any]]:
    """GRPO dataset rows: ``{"prompt": [chat messages], "answer"}``.

    The prompt is **conversational** (a one-message user turn), NOT a raw string: TRL applies
    the model's chat template to conversational prompts before generation, so an instruct model
    answers in chat mode and emits EOS after its answer. A raw-string prompt skips the template,
    the instruct model never stops, and every completion runs to ``max_completion_length``
    (``clipped_ratio=1``) without a parseable answer -> reward 0 -> no gradient. ``answer`` carries
    the per-instance state for the reward (TRL passes extra columns through as kwargs)."""
    return [
        {"prompt": [{"role": "user", "content": row["prompt"]}], "answer": row["answer"]}
        for row in build_prompt_rows(scenario, n_prompts)
    ]


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


def _gkd_diagnostic_prompt_text(tokenizer: Any, row: dict[str, str]) -> str:
    messages = [{"role": "user", "content": row["prompt"]}]
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_chat_template):
        return str(apply_chat_template(messages, tokenize=False, add_generation_prompt=True))
    return row["prompt"]


def collect_hf_token_pressure(
    *,
    student_lm: Any,
    teacher_lm: Any,
    tokenizer: Any,
    prompt_rows: list[dict[str, str]],
    max_new_tokens: int,
    include_token_text: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    """Collect GKD sampled-token pressure without running optimizer steps."""
    import torch  # type: ignore[import-not-found]

    token_pressure = import_module("autocontext.training.autoresearch.token_pressure")
    torch.manual_seed(seed)
    observations: list[Any] = []
    response_lengths: list[int] = []
    device = getattr(student_lm, "device", None)
    teacher_device = getattr(teacher_lm, "device", device)
    for row in prompt_rows:
        encoded = tokenizer(_gkd_diagnostic_prompt_text(tokenizer, row), return_tensors="pt")
        if device is not None:
            encoded = encoded.to(device)
        with torch.no_grad():
            generated = student_lm.generate(
                **encoded,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=getattr(tokenizer, "eos_token_id", None),
            )
            student_logits = student_lm(generated).logits
            teacher_logits = teacher_lm(generated.to(teacher_device)).logits.to(student_logits.device)
            student_logp = torch.log_softmax(student_logits, dim=-1)
            teacher_logp = torch.log_softmax(teacher_logits, dim=-1)
            entropy = -(student_logp.exp() * student_logp).sum(dim=-1)
        prompt_len = int(encoded["input_ids"].shape[1])
        total_len = int(generated.shape[1])
        response_lengths.append(max(total_len - prompt_len, 0))
        for position in range(max(prompt_len - 1, 0), max(total_len - 1, 0)):
            token_id = int(generated[0, position + 1].item())
            token_text = tokenizer.decode([token_id]) if include_token_text else None
            observations.append(
                token_pressure.TokenPressureObservation(
                    position=position,
                    student_logprob=float(student_logp[0, position, token_id].item()),
                    teacher_logprob=float(teacher_logp[0, position, token_id].item()),
                    student_entropy=float(entropy[0, position].item()),
                    token_text=token_text,
                )
            )
    return dict(
        token_pressure.build_token_pressure_report(
            observations,
            backend="trl",
            mode="gkd",
            seed=seed,
            response_lengths=response_lengths,
            include_token_text=include_token_text,
        )
    )


def make_time_budget_callback(time_budget: float) -> Any:
    """A ``transformers.TrainerCallback`` that stops training once ``time_budget`` (s) elapses.

    TRL/transformers loops run to ``num_train_epochs``/``max_steps`` with no wall-clock cap,
    so this enforces the budget at step boundaries (transformers imported lazily so the
    module stays importable without it)."""
    from transformers import TrainerCallback  # type: ignore[import-not-found]

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
    seed: int = 0,
    max_completion_length: int = 512,  # grpo: generation cap (>= task answer length; see build_grpo_config_kwargs)
    grpo_beta: float = 0.04,  # grpo: KL penalty toward the base policy (0.0 = KL-free; nonzero prevents overfitting)
    opd_diagnostics: bool = False,
    opd_diagnostics_debug_tokens: bool = False,
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

    from datasets import Dataset  # type: ignore[import-not-found]
    from peft import LoraConfig  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]

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

    pressure_report: dict[str, Any] | None = None
    student_lm: Any = None
    teacher_lm: Any = None
    prompt_rows: list[dict[str, str]] = []
    if mode == "gkd":
        gkd_config_cls, gkd_trainer_cls = _import_gkd()

        # Compare the MODELS' logit vocab (config.vocab_size), not the tokenizer's: GKD's
        # per-token JSD compares logits, and same-family models can have different PADDED
        # vocab (e.g. Qwen2.5 1.5B=151936 vs 7B=152064) while their tokenizers look equal.
        # Catch it here with a clear message instead of a cryptic tensor-shape error in the loss.
        student_lm = AutoModelForCausalLM.from_pretrained(student_model)
        teacher_lm = AutoModelForCausalLM.from_pretrained(teacher_model)
        s_vocab = getattr(student_lm.config, "vocab_size", None)
        t_vocab = getattr(teacher_lm.config, "vocab_size", None)
        if isinstance(s_vocab, int) and isinstance(t_vocab, int):
            assert_vocab_compatible(s_vocab, t_vocab)
        prompt_rows = build_prompt_rows(scenario, n_prompts)
        dataset = Dataset.from_list(build_chat_dataset_rows(scenario, n_prompts))
        gkd_kwargs: dict[str, Any] = dict(
            output_dir=str(output_dir),
            teacher_model=teacher_model,
            learning_rate=learning_rate,
            max_steps=max_steps,
            seed=seed,
        )
        if batch_size > 0:
            gkd_kwargs["per_device_train_batch_size"] = batch_size
        args = gkd_config_cls(**build_gkd_config_kwargs(**gkd_kwargs))
        trainer = gkd_trainer_cls(
            model=student_lm,
            teacher_model=teacher_lm,
            args=args,
            processing_class=tokenizer,
            train_dataset=dataset,
            peft_config=peft_config,
            callbacks=callbacks,
        )
    else:  # grpo
        from trl import GRPOConfig, GRPOTrainer  # type: ignore[import-not-found]

        dataset = Dataset.from_list(build_prompt_dataset_rows(scenario, n_prompts))
        grpo_kwargs: dict[str, Any] = dict(
            output_dir=str(output_dir),
            learning_rate=learning_rate,
            max_steps=max_steps,
            seed=seed,
            max_completion_length=max_completion_length,
            beta=grpo_beta,
        )
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
    if mode == "gkd" and opd_diagnostics:
        token_pressure = import_module("autocontext.training.autoresearch.token_pressure")
        diag_rows, diag_tokens = token_pressure.bounded_diagnostic_inputs(
            prompt_rows,
            max_completion_length,
            remaining_seconds=float(time_budget) - (time.monotonic() - started),
        )
        if diag_rows:
            pressure_report = collect_hf_token_pressure(
                student_lm=student_lm,
                teacher_lm=teacher_lm,
                tokenizer=tokenizer,
                prompt_rows=diag_rows,
                max_new_tokens=diag_tokens,
                include_token_text=opd_diagnostics_debug_tokens,
                seed=seed,
            )
            token_pressure.write_token_pressure_report(output_dir / "token_pressure_diagnostics.json", pressure_report)
    training_loss = float(getattr(train_output, "training_loss", float("nan")))
    num_steps = float(getattr(getattr(trainer, "state", None), "global_step", 0) or 0)
    # build_trl_metrics gives a FINITE avg_score (negative training loss) so the training
    # runner's keep/discard comparison selects this checkpoint instead of discarding on NaN.
    result = build_trl_metrics(
        training_loss=training_loss,
        num_steps=num_steps,
        training_seconds=time.monotonic() - started,
        peak_memory_mb=_peak_memory_mb(),
    )
    if pressure_report is not None:
        result.update(
            {
                "token_pressure_positive_ratio": float(pressure_report["positive_pressure_ratio"]),
                "token_pressure_negative_ratio": float(pressure_report["negative_pressure_ratio"]),
                "token_pressure_shock_spike_count": float(pressure_report["shock_spike_count"]),
            }
        )
    return result
