"""Linux TRL SFT fine-tuning backend for autoresearch (pure helpers).

The heavy ``run`` function -- which imports TRL/transformers/torch and drives a real
``SFTTrainer`` -- lives in a later task. This module holds only the pure, CI-testable
pieces: converting autoresearch records into TRL ``{"prompt", "completion"}`` SFT pairs,
building the ``SFTConfig`` kwargs as a plain dict (no ``trl`` import), and the wall-clock
deadline logic that a real ``transformers.TrainerCallback`` will wrap in task 3.

The record-to-pair mapping is delegated to the mlx-lm backend's
``records_to_completions`` so both backends train on the identical prompt/completion
contract (per-record prompt fallback, verbatim text vs JSON-serialized strategy,
reason-then-construct completions).
"""

from __future__ import annotations

import importlib.util
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.mlxlm_backend import records_to_completions

# Runtime deps the TRL SFT ``run`` path needs; all imported lazily so the module (and its
# pure-helper tests) load without them. The preflight checks these up front so a missing one
# fails fast with an actionable message rather than tens of seconds into a fine-tune.
_SFT_RUNTIME_DEPS = ("trl", "transformers", "peft", "torch", "datasets")


def sft_pairs_from_records(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert autoresearch records into TRL SFT ``{"prompt", "completion"}`` pairs.

    A thin adapter over the mlx-lm backend's ``records_to_completions``: each record
    supplies its own ``prompt`` (dataset-style agent tasks) and the mapping serializes a
    dict strategy to JSON, keeps a text strategy verbatim, and prepends any teacher
    ``reasoning`` to the completion. Records without their own prompt fall back to the
    empty task prompt (SFT pairs come from curated per-record data, not a shared prompt).
    """
    return records_to_completions(records, task_prompt="")


def build_sft_config_kwargs(
    *,
    output_dir: Path,
    max_steps: int,
    batch_size: int,
    learning_rate: float,
) -> dict[str, Any]:
    """Build the plain-dict kwargs for a TRL ``SFTConfig`` (no ``trl`` import).

    Every key is a real ``SFTConfig`` field, so the returned dict is directly
    ``SFTConfig(**kwargs)``-safe. The wall-clock deadline is not an ``SFTConfig`` field:
    the caller already holds ``deadline_seconds`` and passes it straight to
    :class:`DeadlineCallback`, so it is never threaded through these kwargs.
    """
    return {
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "per_device_train_batch_size": batch_size,
        "learning_rate": learning_rate,
    }


class DeadlineCallback:
    """Pure wall-clock stop logic for the TRL trainer (no ``transformers`` import).

    Task 3 wraps this in a real ``TrainerCallback`` whose ``on_step_end`` sets
    ``control.should_training_stop`` from :meth:`should_stop`. ``should_stop_at`` exposes
    the same comparison against an explicit elapsed time so tests drive it deterministically.
    """

    def __init__(self, deadline_seconds: float, clock: Callable[[], float]) -> None:
        self._deadline_seconds = deadline_seconds
        self._clock = clock
        self._start = clock()

    def should_stop_at(self, elapsed_seconds: float) -> bool:
        """True once ``elapsed_seconds`` has reached the deadline."""
        return elapsed_seconds >= self._deadline_seconds

    def should_stop(self) -> bool:
        """True once the clock has advanced past the deadline since construction."""
        return self.should_stop_at(self._clock() - self._start)


# ---------------------------------------------------------------------------
# Heavy run path (gated; needs trl + transformers + peft + torch + datasets)
# ---------------------------------------------------------------------------


def _preflight_sft_deps() -> None:
    """Fail fast if any TRL-SFT runtime dependency is missing (mirrors ``_preflight_backend_deps``).

    A real fine-tune does tens of seconds of work before the adapter is written, so a
    dependency missing only at save time would crash after all of it. Check every heavy import
    up front and raise an actionable ``RuntimeError`` naming exactly what is missing.
    """
    missing = [name for name in _SFT_RUNTIME_DEPS if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            "trl SFT fine-tuning requires trl + transformers + peft + torch + datasets; "
            f"missing: {', '.join(missing)}. Install with: uv pip install trl peft transformers datasets torch"
        )


def _load_sft_runtime() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Lazily import the heavy TRL/transformers/peft/datasets classes behind one seam.

    Returns ``(SFTConfig, SFTTrainer, LoraConfig, Dataset, AutoModelForCausalLM, AutoTokenizer,
    TrainerCallback)``. Isolating every heavy import here lets the mock-the-run test patch this
    single function with fakes and exercise the whole runner on a machine without ``trl``.
    """
    from datasets import Dataset  # type: ignore[import-not-found]
    from peft import LoraConfig  # type: ignore[import-not-found]
    from transformers import (  # type: ignore[import-not-found]
        AutoModelForCausalLM,
        AutoTokenizer,
        TrainerCallback,
    )
    from trl import SFTConfig, SFTTrainer  # type: ignore[import-not-found]

    return SFTConfig, SFTTrainer, LoraConfig, Dataset, AutoModelForCausalLM, AutoTokenizer, TrainerCallback


def _make_deadline_callback(trainer_callback_cls: Any, deadline_seconds: float) -> Any:
    """Wrap :class:`DeadlineCallback` in a real ``transformers.TrainerCallback``.

    ``transformers`` loops run to ``max_steps`` with no wall-clock cap; this stops training at
    the next step boundary once the deadline passes. The pure comparison lives in
    :class:`DeadlineCallback` so it stays unit-testable without ``transformers``; this thin
    subclass only bridges it onto the trainer's ``control.should_training_stop``.
    """
    deadline = DeadlineCallback(deadline_seconds, clock=time.perf_counter)

    class _DeadlineTrainerCallback(trainer_callback_cls):  # type: ignore[misc, valid-type]
        def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
            if deadline.should_stop():
                control.should_training_stop = True
            return control

    return _DeadlineTrainerCallback()


def _sft_max_steps(time_budget: int) -> int:
    """Derive the optimizer-step cap from the wall-clock budget.

    One step per ~10s of budget, clamped to ``[1, 100]`` (100 is the pretrained-adapter default
    the from-scratch backends do not use). The :class:`DeadlineCallback` still enforces the exact
    wall-clock stop; this only bounds the schedule so a tiny budget does not target 100 steps.
    """
    return max(1, min(100, time_budget // 10))


def _mean_curated_score(records: list[dict[str, Any]]) -> float:
    """Mean ``score`` over the curated records (0.0 when empty), for the metrics ``avg_score``."""
    scores = [float(record.get("score", 0.0)) for record in records]
    return sum(scores) / len(scores) if scores else 0.0


def _trainable_params_m(trainer: Any) -> float:
    """Trainable parameter count of the trainer's (LoRA-wrapped) model, in millions.

    Best-effort: reads ``trainer.model.parameters()`` and sums the ``requires_grad`` ones. Any
    failure (a fake trainer in tests, an API shift) yields ``0.0`` rather than breaking the run.
    """
    try:
        total = sum(int(p.numel()) for p in trainer.model.parameters() if getattr(p, "requires_grad", False))
    except Exception:
        return 0.0
    return total / 1e6


def run_sft_training(
    *,
    scenario_name: str,  # noqa: ARG001 - seam-signature parity; SFT trains on the curated file, not a live scenario
    data_path: Path,
    output_dir: Path,
    base_model: str,
    time_budget: int,
    memory_limit_mb: int,
    deadline_seconds: float | None = None,
) -> dict[str, float]:
    """Fine-tune ``base_model`` over the curated jsonl with TRL's ``SFTTrainer`` (LoRA).

    Cross-platform (Linux / CPU / NVIDIA); imports ``trl`` / ``transformers`` / ``peft`` /
    ``torch`` / ``datasets`` lazily via :func:`_load_sft_runtime`, after a preflight that fails
    fast if any is missing. Loads + curates the records (``elite_fraction=1.0``, ``dedupe=True``
    to honor the curate crash-window mitigation, matching the mlxlm path), converts them to TRL
    ``{"prompt", "completion"}`` pairs, and trains a LoRA adapter. When ``deadline_seconds`` is
    set a real ``TrainerCallback`` wrapping :class:`DeadlineCallback` stops training at the next
    step boundary once the wall-clock deadline passes. The adapter is saved to
    ``output_dir/"adapters"`` -- the checkpoint path the ambient train seam reads.
    """
    from autocontext.training.autoresearch.data_selection import prepare_training_records
    from autocontext.training.autoresearch.train import _all_records, _peak_memory_mb

    _preflight_sft_deps()
    (
        sft_config_cls,
        sft_trainer_cls,
        lora_config_cls,
        dataset_cls,
        auto_model_cls,
        auto_tokenizer_cls,
        trainer_callback_cls,
    ) = _load_sft_runtime()

    curated = prepare_training_records(_all_records(data_path), elite_fraction=1.0, dedupe=True)
    if not curated:
        raise ValueError(f"no training records after curation in {data_path}")
    pairs = sft_pairs_from_records(curated)
    dataset = dataset_cls.from_list(pairs)

    output_dir.mkdir(parents=True, exist_ok=True)
    model = auto_model_cls.from_pretrained(base_model)
    tokenizer = auto_tokenizer_cls.from_pretrained(base_model)

    config = sft_config_cls(
        **build_sft_config_kwargs(
            output_dir=output_dir,
            max_steps=_sft_max_steps(time_budget),
            batch_size=1,
            learning_rate=2e-4,
        )
    )
    lora = lora_config_cls(r=8, lora_alpha=16, task_type="CAUSAL_LM")

    callbacks: list[Any] = []
    if deadline_seconds is not None:
        callbacks.append(_make_deadline_callback(trainer_callback_cls, deadline_seconds))

    trainer = sft_trainer_cls(
        model=model,
        args=config,
        train_dataset=dataset,
        peft_config=lora,
        processing_class=tokenizer,
        callbacks=callbacks,
    )
    started = time.perf_counter()
    trainer.train()
    elapsed = time.perf_counter() - started
    # Save to output_dir/"adapters": the exact checkpoint path the ambient train seam
    # (training_backend.run_training) reports for this run.
    trainer.save_model(str(output_dir / "adapters"))

    return {
        "training_seconds": elapsed,
        "num_records": float(len(pairs)),
        "valid_rate": 1.0,
        "avg_score": _mean_curated_score(curated),
        "peak_memory_mb": min(_peak_memory_mb(), float(memory_limit_mb)),
        "num_params_m": _trainable_params_m(trainer),
    }
