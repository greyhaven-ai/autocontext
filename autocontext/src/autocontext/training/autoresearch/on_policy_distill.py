"""On-policy distillation (Apple Silicon / MLX): dense per-token reverse-KL from a teacher.

The student samples completions ON-POLICY; the training signal is the per-token reverse KL
``KL(student || teacher)`` over the student-generated positions, a dense signal at every
token (vs RLVR's single sparse reward per episode). Reverse KL is mode-seeking and cannot
be reward-hacked: low KL always means the student moved toward genuine teacher behavior.

This module is the MLX-native build (mlx-lm-lora has no GKD/distillation mode). The loss
kernel here is the differentiable full-distribution reverse KL; the teacher is frozen
(stop-gradient), so gradient flows only into the student. A CUDA path via TRL's GKDTrainer
is the cross-platform counterpart for larger runs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import mlx.core as mx
import mlx.nn as nn

_EPS = 1e-8

# Both models must share a tokenizer, so default teacher/student are the same family
# (Qwen2.5). The student is the capable RLVR-grade base; the teacher is larger.
DEFAULT_STUDENT_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
DEFAULT_TEACHER_MODEL = "mlx-community/Qwen2.5-7B-Instruct-4bit"
_LORA_PARAMETERS = {"rank": 8, "dropout": 0.0, "scale": 20.0}

__all__ = [
    "DEFAULT_STUDENT_MODEL",
    "DEFAULT_TEACHER_MODEL",
    "assert_vocab_compatible",
    "distill_loss",
    "distill_over_prompts",
    "distill_update_step",
    "on_policy_distill_step",
    "reverse_kl_per_token",
    "run_on_policy_distillation",
    "sample_completion",
]


def _log_softmax(logits: mx.array, axis: int = -1) -> mx.array:
    return logits - mx.logsumexp(logits, axis=axis, keepdims=True)


def reverse_kl_per_token(
    student_logits: mx.array,
    teacher_logits: mx.array,
    completion_mask: mx.array,
    *,
    temperature: float = 1.0,
) -> mx.array:
    """Mean per-token reverse KL ``KL(student || teacher)`` over masked positions.

    ``student_logits`` / ``teacher_logits`` are ``[B, T, V]``; ``completion_mask`` is
    ``[B, T]`` with ``1.0`` on the student-generated positions to distill on. The teacher
    is treated as frozen (stop-gradient), so gradient flows only into the student. Returns
    ``0.0`` for an all-zero mask (no NaN). ``temperature`` softens both distributions.
    """
    teacher_logits = mx.stop_gradient(teacher_logits)
    inv_t = 1.0 / temperature
    student_logp = _log_softmax(student_logits * inv_t, axis=-1)
    teacher_logp = _log_softmax(teacher_logits * inv_t, axis=-1)
    student_p = mx.exp(student_logp)

    # reverse KL per position: sum_v p_student(v) * (log p_student(v) - log p_teacher(v))
    kl = mx.sum(student_p * (student_logp - teacher_logp), axis=-1)  # [B, T]

    masked = kl * completion_mask
    denom = mx.sum(completion_mask)
    return mx.sum(masked) / mx.maximum(denom, _EPS)


def distill_loss(
    student_model: Any,
    teacher_model: Any,
    full_ids: mx.array,
    completion_mask: mx.array,
    *,
    temperature: float = 1.0,
) -> mx.array:
    """Reverse-KL distillation loss over ``full_ids`` (one forward per model).

    ``full_ids`` is ``[B, T]`` (prompt + completion); ``completion_mask`` marks the
    positions whose next-token distribution should match the teacher's. Differentiable in
    the student; the teacher is frozen inside :func:`reverse_kl_per_token`.
    """
    student_logits = student_model(full_ids)
    teacher_logits = teacher_model(full_ids)
    return reverse_kl_per_token(student_logits, teacher_logits, completion_mask, temperature=temperature)


def distill_update_step(
    student_model: Any,
    teacher_model: Any,
    optimizer: Any,
    full_ids: mx.array,
    completion_mask: mx.array,
    *,
    temperature: float = 1.0,
) -> float:
    """One gradient step minimizing the reverse-KL distillation loss on a FIXED batch."""

    def loss_fn(model: Any) -> mx.array:
        return distill_loss(model, teacher_model, full_ids, completion_mask, temperature=temperature)

    loss, grads = nn.value_and_grad(student_model, loss_fn)(student_model)
    optimizer.update(student_model, grads)
    mx.eval(student_model.parameters(), optimizer.state)
    return float(loss)


def sample_completion(
    model: Any,
    prompt_ids: mx.array,
    *,
    max_tokens: int,
    temperature: float = 1.0,
) -> tuple[mx.array, mx.array]:
    """Roll out ``max_tokens`` tokens ON-POLICY from ``model`` given ``prompt_ids``.

    Returns ``(full_ids, completion_mask)``: ``full_ids`` is ``[B, P + max_tokens]`` and
    ``completion_mask`` is ``[B, P + max_tokens]`` with ``1.0`` on the positions that
    PREDICT a generated token (indices ``P-1 .. P+max_tokens-2``), i.e. exactly the
    positions distilled against the teacher. ``temperature <= 0`` is greedy (argmax).
    Fixed-length (no early EOS) so the batch stays rectangular.
    """
    prompt_len = int(prompt_ids.shape[1])
    ids = prompt_ids
    for _ in range(max_tokens):
        next_logits = model(ids)[:, -1, :]  # [B, V]
        if temperature and temperature > 0:
            next_tok = mx.random.categorical(next_logits * (1.0 / temperature))
        else:
            next_tok = mx.argmax(next_logits, axis=-1)
        ids = mx.concatenate([ids, next_tok[:, None].astype(prompt_ids.dtype)], axis=1)

    total_len = prompt_len + max_tokens
    positions = mx.arange(total_len)
    row_mask = ((positions >= (prompt_len - 1)) & (positions <= (total_len - 2))).astype(mx.float32)
    completion_mask = mx.broadcast_to(row_mask[None, :], (int(ids.shape[0]), total_len))
    return ids, completion_mask


def on_policy_distill_step(
    student_model: Any,
    teacher_model: Any,
    optimizer: Any,
    prompt_ids: mx.array,
    *,
    max_tokens: int,
    sample_temperature: float = 1.0,
    kl_temperature: float = 1.0,
) -> float:
    """One on-policy distillation step: student rolls out, then a reverse-KL update.

    The rollout is drawn from the student (no gradient), then the student is trained to
    match the teacher's per-token distribution on that self-generated sequence.
    """
    full_ids, completion_mask = sample_completion(
        student_model, prompt_ids, max_tokens=max_tokens, temperature=sample_temperature
    )
    full_ids = mx.stop_gradient(full_ids)
    return distill_update_step(student_model, teacher_model, optimizer, full_ids, completion_mask, temperature=kl_temperature)


def assert_vocab_compatible(student_vocab: int, teacher_vocab: int) -> None:
    """Reject a teacher/student tokenizer mismatch up front with a clear message.

    On-policy distillation compares per-token distributions, so the two models must share
    a vocabulary; mismatched sizes otherwise surface as an opaque logit-shape error deep in
    the loss after both (large) models have loaded.
    """
    if student_vocab != teacher_vocab:
        raise ValueError(
            "on-policy distillation requires a shared tokenizer: student vocab "
            f"{student_vocab} != teacher vocab {teacher_vocab}. Use teacher and student "
            "from the same model family."
        )


def distill_over_prompts(
    student_model: Any,
    teacher_model: Any,
    optimizer: Any,
    prompts: list[mx.array],
    *,
    iters: int,
    max_tokens: int,
    sample_temperature: float = 1.0,
    kl_temperature: float = 1.0,
    time_budget: float | None = None,
) -> dict[str, float]:
    """Run up to ``iters`` on-policy distillation steps, cycling through ``prompts``.

    Each ``prompts`` entry is a ``[1, P]`` token array. ``time_budget`` (seconds), if set,
    stops the loop at the next iteration boundary once exceeded, so an in-process run cannot
    overrun indefinitely on expensive rollouts. Returns ``num_steps`` plus the ``final_loss``
    and ``mean_loss`` over the run (the training loop body of the backend).
    """
    if not prompts:
        return {"num_steps": 0.0, "final_loss": 0.0, "mean_loss": 0.0}
    losses: list[float] = []
    started = time.monotonic()
    for step in range(iters):
        if time_budget is not None and (time.monotonic() - started) >= time_budget:
            break
        prompt_ids = prompts[step % len(prompts)]
        losses.append(
            on_policy_distill_step(
                student_model,
                teacher_model,
                optimizer,
                prompt_ids,
                max_tokens=max_tokens,
                sample_temperature=sample_temperature,
                kl_temperature=kl_temperature,
            )
        )
    if not losses:
        return {"num_steps": 0.0, "final_loss": 0.0, "mean_loss": 0.0}
    return {
        "num_steps": float(len(losses)),
        "final_loss": losses[-1],
        "mean_loss": sum(losses) / len(losses),
    }


def _tokenize_prompts(tokenizer: Any, rows: list[dict[str, str]]) -> list[mx.array]:
    """Turn scenario prompt rows into ``[1, P]`` token arrays via the chat template."""
    prompts: list[mx.array] = []
    for row in rows:
        ids = tokenizer.apply_chat_template([{"role": "user", "content": row["prompt"]}], add_generation_prompt=True)
        prompts.append(mx.array([list(ids)]))
    return prompts


def run_on_policy_distillation(
    *,
    scenario_name: str,
    output_dir: Path,
    teacher_model: str = DEFAULT_TEACHER_MODEL,
    student_model: str = DEFAULT_STUDENT_MODEL,
    n_prompts: int = 64,
    iters: int = 100,
    max_tokens: int = 256,
    learning_rate: float = 1e-5,
    num_layers: int = 8,
    sample_temperature: float = 1.0,
    kl_temperature: float = 1.0,
    assess_samples: int = 8,
    assess_temperature: float = 0.0,
    register_import: str | None = None,
    time_budget: int = 3600,
    memory_limit_mb: int = 16384,
) -> dict[str, float]:
    """Distill a teacher into a LoRA student IN-PROCESS via on-policy reverse-KL, then assess.

    There is no distillation CLI to shell out to (mlx-lm-lora has no GKD mode), so this runs
    the training loop in-process: the student (LoRA) samples on-policy and is trained to
    match the frozen teacher's per-token distribution. Teacher and student MUST share a
    tokenizer (default: same Qwen2.5 family). ``register_import`` registers a consumer-repo
    scenario in this process before lookup. Returns backend-standard summary metrics.
    """
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten
    from mlx_lm import load
    from mlx_lm.tuner.utils import linear_to_lora_layers

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.grpo_backend import build_prompt_rows
    from autocontext.training.autoresearch.mlxlm_backend import _assess_mlxlm, scenario_task_prompt
    from autocontext.training.autoresearch.train import _peak_memory_mb, _preflight_backend_deps

    _preflight_backend_deps("mlxlm")  # needs mlx-lm (load + tuner)
    teacher_model = teacher_model or DEFAULT_TEACHER_MODEL
    student_model = student_model or DEFAULT_STUDENT_MODEL
    if register_import:
        exec(register_import, {})  # noqa: S102 - trusted operator-supplied registration hook
    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")
    scenario = SCENARIO_REGISTRY[scenario_name]()

    started = time.monotonic()  # spans model loads + the loop, so loads count against time_budget
    loaded_teacher = load(teacher_model)
    teacher, teacher_tok = loaded_teacher[0], loaded_teacher[1]
    teacher.freeze()
    loaded_student = load(student_model)
    student, tokenizer = loaded_student[0], loaded_student[1]
    student.freeze()
    # Reject a tokenizer mismatch before training (else an opaque logit-shape error later).
    student_vocab = getattr(tokenizer, "vocab_size", None)
    teacher_vocab = getattr(teacher_tok, "vocab_size", None)
    if isinstance(student_vocab, int) and isinstance(teacher_vocab, int):
        assert_vocab_compatible(student_vocab, teacher_vocab)
    linear_to_lora_layers(student, num_layers, _LORA_PARAMETERS)

    rows = build_prompt_rows(scenario, n_prompts)
    prompts = _tokenize_prompts(tokenizer, rows)
    optimizer = optim.Adam(learning_rate=learning_rate)

    loop_budget = max(0.0, float(time_budget) - (time.monotonic() - started))
    loop = distill_over_prompts(
        student,
        teacher,
        optimizer,
        prompts,
        iters=iters,
        max_tokens=max_tokens,
        sample_temperature=sample_temperature,
        kl_temperature=kl_temperature,
        time_budget=loop_budget,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = output_dir / "adapters"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    adapter_weights = dict(tree_flatten(student.trainable_parameters()))
    mx.save_safetensors(str(adapter_dir / "adapters.safetensors"), adapter_weights)
    (adapter_dir / "adapter_config.json").write_text(
        json.dumps({"fine_tune_type": "lora", "num_layers": num_layers, "lora_parameters": _LORA_PARAMETERS}),
        encoding="utf-8",
    )

    metrics = _assess_mlxlm(
        base_model=student_model,
        adapter_dir=adapter_dir,
        scenario=scenario,
        task_prompt=scenario_task_prompt(scenario),
        n_samples=assess_samples,
        temperature=assess_temperature,
        top_k=0,
        score_conditioned=False,
    )
    return {
        "avg_score": metrics["avg_score"],
        "valid_rate": metrics["valid_rate"],
        "val_loss": float("nan"),
        "training_seconds": time.monotonic() - started,
        "peak_memory_mb": min(_peak_memory_mb(), float(memory_limit_mb)),
        "num_steps": loop["num_steps"],
        "final_loss": loop["final_loss"],
        "mean_loss": loop["mean_loss"],
        "num_records": float(len(prompts)),
        "num_params_m": 0.0,
        "depth": 0.0,
    }
