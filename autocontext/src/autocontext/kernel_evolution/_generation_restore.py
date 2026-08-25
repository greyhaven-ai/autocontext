"""Provider-generator restore helpers kept outside the public generation module."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from autocontext.kernel_evolution._generation_replay import (
    revalidated_generation_record,
    validate_generation_replay,
)
from autocontext.kernel_evolution.models import content_digest


def restored_generation_history(generator: Any, history: Iterable[Any]) -> list[Any]:
    restored = [revalidated_generation_record(item) for item in history]
    validate_generation_replay(
        restored,
        (),
        generator.budget,
        expected_provider=generator.provider_id,
        expected_system_prompt_digest=content_digest(generator.system_prompt.encode("utf-8")),
        expected_source_suffix=generator.source_suffix,
        expected_entrypoint=generator.entrypoint,
    )
    return restored


def restored_pending_failures(
    generator: Any,
    proposal_index: int,
    failures: Iterable[Any],
    *,
    backoff_completed: bool,
) -> list[Any]:
    restored = [revalidated_generation_record(item) for item in failures]
    if any(not failure.retryable for failure in restored):
        raise ValueError("a non-retryable generation failure cannot be resumed")
    if restored and float(restored[-1].retry_delay_seconds) > 0.0 and not backoff_completed:
        raise ValueError("delayed generation failure requires a completed-backoff receipt")
    validate_generation_replay(
        generator._history,
        restored,
        generator.budget,
        expected_provider=generator.provider_id,
        expected_system_prompt_digest=content_digest(generator.system_prompt.encode("utf-8")),
        expected_source_suffix=generator.source_suffix,
        expected_entrypoint=generator.entrypoint,
        resumable_proposal=proposal_index,
    )
    return restored


__all__ = ["restored_generation_history", "restored_pending_failures"]
