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
from importlib import import_module
from pathlib import Path
from typing import Any

import mlx.core as mx  # type: ignore[import-not-found]
import mlx.nn as nn  # type: ignore[import-not-found]

# Re-exported from the pure (mlx-free) shared module so the cross-platform trl backend can
# use it without importing this mlx-scoped module; kept importable here for back-compat.
from autocontext.training.autoresearch.distill_common import assert_vocab_compatible
from autocontext.training.model_defaults import OPD_DEFAULT_STUDENT_MODEL, OPD_DEFAULT_TEACHER_MODEL

_EPS = 1e-8

# Teacher and student must share the LOGIT vocab dimension (reverse_kl_per_token compares
# logits over vocab). Qwen2.5 0.5B/1.5B/3B share vocab 151936, but 7B+ use 152064, so the
# default teacher is 3B (NOT 7B) -- a 7B teacher with a 1.5B student fails on a vocab shape
# mismatch. 3B still gives a real teacher>student capability gap for distillation.
DEFAULT_STUDENT_MODEL = OPD_DEFAULT_STUDENT_MODEL
DEFAULT_TEACHER_MODEL = OPD_DEFAULT_TEACHER_MODEL
_LORA_PARAMETERS = {"rank": 8, "dropout": 0.0, "scale": 20.0}

__all__ = [
    "DEFAULT_STUDENT_MODEL",
    "DEFAULT_TEACHER_MODEL",
    "assert_vocab_compatible",
    "distill_loss",
    "collect_token_pressure_for_prompts",
    "distill_over_prompts",
    "distill_update_step",
    "_model_logit_vocab",
    "on_policy_distill_step",
    "reverse_kl_per_token",
    "run_on_policy_distillation",
    "sample_completion",
]


def _log_softmax(logits: mx.array, axis: int = -1) -> mx.array:
    return logits - mx.logsumexp(logits, axis=axis, keepdims=True)


def _model_logit_vocab(model: Any) -> int:
    """The model's output logit vocab size, via a 1-token forward.

    This is the exact dimension :func:`reverse_kl_per_token` compares, so it catches
    padded-vocab mismatches (e.g. Qwen2.5 1.5B=151936 vs 7B=152064) that ``tokenizer.vocab_size``
    misses; reading logits is also robust across mlx_lm architectures vs a config attr.
    """
    return int(model(mx.array([[0]])).shape[-1])


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


def collect_token_pressure_for_prompts(
    student_model: Any,
    teacher_model: Any,
    prompts: list[mx.array],
    *,
    max_tokens: int,
    sample_temperature: float = 1.0,
    include_token_text: bool = False,
    tokenizer: Any | None = None,
    backend: str = "opd",
    mode: str = "opd",
    seed: int = 0,
) -> dict[str, Any]:
    """Collect teacher-vs-student sampled-token pressure without updating weights."""
    token_pressure = import_module("autocontext.training.autoresearch.token_pressure")
    mx.random.seed(seed)
    observations: list[Any] = []
    response_lengths: list[int] = []
    for prompt_ids in prompts:
        full_ids, completion_mask = sample_completion(
            student_model,
            prompt_ids,
            max_tokens=max_tokens,
            temperature=sample_temperature,
        )
        full_ids = mx.stop_gradient(full_ids)
        student_logp = _log_softmax(student_model(full_ids), axis=-1)
        teacher_logp = _log_softmax(mx.stop_gradient(teacher_model(full_ids)), axis=-1)
        student_entropy = -mx.sum(mx.exp(student_logp) * student_logp, axis=-1)
        for row in range(int(full_ids.shape[0])):
            ids = [int(token) for token in full_ids[row].tolist()]
            mask = [float(value) for value in completion_mask[row].tolist()]
            response_lengths.append(sum(1 for value in mask if value > 0))
            for position, active in enumerate(mask):
                if active <= 0 or position + 1 >= len(ids):
                    continue
                token_id = ids[position + 1]
                token_text = tokenizer.decode([token_id]) if include_token_text and tokenizer is not None else None
                observations.append(
                    token_pressure.TokenPressureObservation(
                        position=position,
                        student_logprob=float(student_logp[row, position, token_id]),
                        teacher_logprob=float(teacher_logp[row, position, token_id]),
                        student_entropy=float(student_entropy[row, position]),
                        token_text=token_text,
                    )
                )
    return dict(
        token_pressure.build_token_pressure_report(
            observations,
            backend=backend,
            mode=mode,
            seed=seed,
            response_lengths=response_lengths,
            include_token_text=include_token_text,
        )
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
    seed: int = 0,
    opd_diagnostics: bool = False,
    opd_diagnostics_debug_tokens: bool = False,
) -> dict[str, float]:
    """Distill a teacher into a LoRA student IN-PROCESS via on-policy reverse-KL, then assess.

    There is no distillation CLI to shell out to (mlx-lm-lora has no GKD mode), so this runs
    the training loop in-process: the student (LoRA) samples on-policy and is trained to
    match the frozen teacher's per-token distribution. Teacher and student MUST share a
    tokenizer (default: same Qwen2.5 family). ``register_import`` registers a consumer-repo
    scenario in this process before lookup. Returns backend-standard summary metrics.
    """
    import mlx.optimizers as optim  # type: ignore[import-not-found]
    from mlx.utils import tree_flatten  # type: ignore[import-not-found]
    from mlx_lm import load  # type: ignore[import-not-found]
    from mlx_lm.tuner.utils import linear_to_lora_layers  # type: ignore[import-not-found]

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
    teacher = load(teacher_model)[0]
    teacher.freeze()
    loaded_student = load(student_model)
    student, tokenizer = loaded_student[0], loaded_student[1]
    student.freeze()
    # Reject a vocab mismatch before training, comparing the MODELS' LOGIT vocab (what
    # reverse_kl_per_token actually compares) -- NOT tokenizer.vocab_size, which can match
    # while the padded logit dims differ (e.g. Qwen2.5 1.5B=151936 vs 7B=152064) and would
    # otherwise crash deep in the loss with an opaque tensor-shape error.
    assert_vocab_compatible(_model_logit_vocab(student), _model_logit_vocab(teacher))
    linear_to_lora_layers(student, num_layers, _LORA_PARAMETERS)

    rows = build_prompt_rows(scenario, n_prompts)
    prompts = _tokenize_prompts(tokenizer, rows)
    optimizer = optim.Adam(learning_rate=learning_rate)

    output_dir.mkdir(parents=True, exist_ok=True)
    pressure_report: dict[str, Any] | None = None
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

    if opd_diagnostics:
        token_pressure = import_module("autocontext.training.autoresearch.token_pressure")
        diag_prompts, diag_tokens = token_pressure.bounded_diagnostic_inputs(
            prompts,
            max_tokens,
            remaining_seconds=float(time_budget) - (time.monotonic() - started),
        )
        if diag_prompts:
            pressure_report = collect_token_pressure_for_prompts(
                student,
                teacher,
                diag_prompts,
                max_tokens=diag_tokens,
                sample_temperature=sample_temperature,
                include_token_text=opd_diagnostics_debug_tokens,
                tokenizer=tokenizer,
                backend="opd",
                mode="opd",
                seed=seed,
            )
            token_pressure.write_token_pressure_report(output_dir / "token_pressure_diagnostics.json", pressure_report)

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
    result = {
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
    if pressure_report is not None:
        result.update(
            {
                "token_pressure_positive_ratio": float(pressure_report["positive_pressure_ratio"]),
                "token_pressure_negative_ratio": float(pressure_report["negative_pressure_ratio"]),
                "token_pressure_shock_spike_count": float(pressure_report["shock_spike_count"]),
            }
        )
    return result
