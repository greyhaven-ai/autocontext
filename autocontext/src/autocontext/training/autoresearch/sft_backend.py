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

from collections.abc import Callable
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.mlxlm_backend import records_to_completions


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
    deadline_seconds: float,
) -> dict[str, Any]:
    """Build the plain-dict kwargs for a TRL ``SFTConfig`` (no ``trl`` import).

    ``deadline_seconds`` is recorded so the caller can construct the matching
    ``DeadlineCallback``; it is not an ``SFTConfig`` field and the caller pops it before
    passing the remaining kwargs to ``SFTConfig(**kwargs)``.
    """
    return {
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "per_device_train_batch_size": batch_size,
        "learning_rate": learning_rate,
        "deadline_seconds": deadline_seconds,
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
