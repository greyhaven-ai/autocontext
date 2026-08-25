"""Fail-closed replay validation for durable generation activity."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any, Protocol, TypeVar, cast

from autocontext.kernel_evolution._generation_errors import KernelGenerationValidationError
from autocontext.kernel_evolution._generation_source import validate_kernel_source
from autocontext.kernel_evolution._generation_usage import (
    validate_estimated_generation_cost,
    validate_generation_usage_receipt,
)
from autocontext.kernel_evolution.models import KernelCandidate, content_digest


class _Budget(Protocol):
    proposal_cap: int
    max_retries_per_proposal: int
    max_output_tokens_per_call: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_total_tokens: int
    max_cost_usd: float
    max_wall_seconds: float
    retry_backoff_seconds: float
    max_source_bytes: int


_RecordT = TypeVar("_RecordT")


def revalidated_generation_record(record: _RecordT) -> _RecordT:
    """Force model validators to replay even for an unchecked ``model_copy``."""
    validator = getattr(type(record), "model_validate", None)
    dumper = getattr(record, "model_dump", None)
    if not callable(validator) or not callable(dumper):
        raise ValueError("generation replay records must be validated models")
    return cast(_RecordT, validator(dumper(mode="python", warnings="error")))


def _validate_call(record: Any, budget: _Budget, *, require_directional: bool) -> None:
    validate_generation_usage_receipt(record.usage, require_directional=require_directional)
    if record.usage.output_tokens > budget.max_output_tokens_per_call:
        raise ValueError("generation receipt exceeds the per-call output-token budget")
    validate_estimated_generation_cost(record)
    if not math.isfinite(float(record.cost_usd)) or float(record.cost_usd) < 0.0:
        raise ValueError("generation receipt cost must be finite and non-negative")
    if not math.isfinite(float(record.latency_seconds)) or float(record.latency_seconds) < 0.0:
        raise ValueError("generation receipt latency must be finite and non-negative")


def _validate_failure_sequence(
    failures: Sequence[Any],
    budget: _Budget,
    *,
    proposal_index: int,
    provider: str | None,
    followed_by_result: bool,
) -> None:
    if [item.call_index for item in failures] != list(range(1, len(failures) + 1)):
        raise ValueError("generation failure call indexes are not contiguous")
    sequence_provider = provider or (failures[0].provider if failures else None)
    for index, failure in enumerate(failures):
        if failure.schema_version != "autocontext.kernel-generation-failure/v1":
            raise ValueError("generation failure schema is invalid")
        if failure.proposal_index != proposal_index:
            raise ValueError("generation failure belongs to a different proposal")
        if sequence_provider is not None and failure.provider != sequence_provider:
            raise ValueError("generation failure belongs to a different provider")
        followed = followed_by_result or index < len(failures) - 1
        if followed and not failure.retryable:
            raise ValueError("generation retried after a non-retryable failure")
        _validate_call(failure, budget, require_directional=followed or failure.retryable)
        pinned_delay = float(budget.retry_backoff_seconds) * (2 ** (failure.call_index - 1))
        observed_delay = float(failure.retry_delay_seconds)
        if not math.isfinite(observed_delay) or observed_delay < 0.0:
            raise ValueError("generation retry delay must be finite and non-negative")
        if (followed and observed_delay < pinned_delay) or (
            not followed and observed_delay != 0.0 and observed_delay < pinned_delay
        ):
            raise ValueError("generation retry delay disagrees with its pinned exponential backoff")


def _validate_result(
    result: Any,
    budget: _Budget,
    *,
    expected_provider: str | None,
    expected_system_prompt_digest: str | None,
    expected_source_suffix: str | None,
    expected_entrypoint: str | None,
) -> None:
    if result.schema_version != "autocontext.kernel-generation/v1":
        raise ValueError("generation result schema is invalid")
    if expected_provider is not None and result.provider != expected_provider:
        raise ValueError("generation history belongs to a different provider")
    if expected_system_prompt_digest is not None and result.system_prompt_digest != expected_system_prompt_digest:
        raise ValueError("generation history belongs to a different system prompt")
    if (
        expected_source_suffix is not None
        and result.source_suffix != expected_source_suffix
        or expected_entrypoint is not None
        and result.entrypoint != expected_entrypoint
    ):
        raise ValueError("generation history belongs to a different source ABI")
    if len(result.source.encode("utf-8")) > budget.max_source_bytes:
        raise ValueError("generation result exceeds the exact source-byte budget")
    legacy_callable = (
        result.provider == "callable"
        and result.cost_source == "not-billable"
        and result.system_prompt_digest == content_digest(b"legacy callable generation adapter")
        and float(result.latency_seconds) == 0.0
        and result.stop_reason is None
        and result.retry_count == 0
        and not result.failures
    )
    if not legacy_callable:
        try:
            validate_kernel_source(
                result.source,
                source_suffix=result.source_suffix,
                entrypoint=result.entrypoint,
                stop_reason=result.stop_reason,
                max_source_bytes=budget.max_source_bytes,
            )
        except KernelGenerationValidationError as exc:
            raise ValueError(str(exc)) from exc
    candidate = KernelCandidate(
        source=result.source,
        source_suffix=result.source_suffix,
        entrypoint=result.entrypoint,
    )
    if (
        result.response_digest != content_digest(result.source.encode("utf-8"))
        or result.source_digest != candidate.source_digest
        or result.artifact_digest != candidate.artifact_digest
    ):
        raise ValueError("generation history source identity is invalid")
    if result.retry_count != len(result.failures):
        raise ValueError("generation retry count disagrees with its failure prefix")
    if result.retry_count > budget.max_retries_per_proposal:
        raise ValueError("generation receipt exceeds the per-proposal retry budget")
    _validate_failure_sequence(
        result.failures,
        budget,
        proposal_index=result.proposal_index,
        provider=result.provider,
        followed_by_result=True,
    )
    not_billable = result.cost_source == "not-billable"
    if not_billable and (
        float(result.cost_usd) != 0.0
        or result.usage.input_tokens != 0
        or result.usage.output_tokens != 0
        or result.usage.total_tokens != 0
        or any(result.usage.provider_usage.values())
    ):
        raise ValueError("non-billable generation receipt contains provider usage or cost")
    _validate_call(result, budget, require_directional=not not_billable)


def validate_generation_replay(
    results: Iterable[Any],
    terminal_failures: Iterable[Any],
    budget: _Budget,
    *,
    expected_provider: str | None = None,
    expected_system_prompt_digest: str | None = None,
    expected_source_suffix: str | None = None,
    expected_entrypoint: str | None = None,
    resumable_proposal: int | None = None,
    enforce_aggregate_budget: bool = True,
) -> None:
    """Replay structural, accounting, retry, and aggregate budget invariants."""
    restored = tuple(results)
    trailing = tuple(terminal_failures)
    replay_provider = expected_provider or (
        restored[0].provider if restored else trailing[0].provider if trailing else None
    )
    if [item.proposal_index for item in restored] != list(range(1, len(restored) + 1)):
        raise ValueError("generation history must be contiguous from proposal one")
    if len(restored) > budget.proposal_cap:
        raise ValueError("generation history exceeds the proposal budget")
    for result in restored:
        _validate_result(
            result,
            budget,
            expected_provider=replay_provider,
            expected_system_prompt_digest=expected_system_prompt_digest,
            expected_source_suffix=expected_source_suffix,
            expected_entrypoint=expected_entrypoint,
        )
    if trailing:
        proposal_index = len(restored) + 1
        if any(item.proposal_index != proposal_index for item in trailing):
            raise ValueError("terminal generation activity must belong to the next proposal")
        max_failures = budget.max_retries_per_proposal + (0 if resumable_proposal is not None else 1)
        if len(trailing) > max_failures:
            raise ValueError("generation failure exceeds the per-proposal retry budget")
        _validate_failure_sequence(
            trailing,
            budget,
            proposal_index=proposal_index,
            provider=replay_provider,
            followed_by_result=resumable_proposal is not None,
        )
    if resumable_proposal is not None and resumable_proposal != len(restored) + 1:
        raise ValueError("pending failures must belong to the next incomplete proposal")
    records: list[Any] = [record for result in restored for record in (*result.failures, result)]
    records.extend(trailing)
    totals: tuple[tuple[float, float, str], ...] = (
        (float(sum(item.usage.input_tokens for item in records)), budget.max_total_input_tokens, "input_tokens"),
        (float(sum(item.usage.output_tokens for item in records)), budget.max_total_output_tokens, "output_tokens"),
        (float(sum(item.usage.total_tokens for item in records)), budget.max_total_tokens, "total_tokens"),
        (sum(float(item.cost_usd) for item in records), float(budget.max_cost_usd), "cost_usd"),
        (
            sum(
                float(item.latency_seconds)
                + (float(item.retry_delay_seconds) if hasattr(item, "retry_delay_seconds") else 0.0)
                for item in records
            ),
            float(budget.max_wall_seconds),
            "wall_seconds",
        ),
    )
    exceeded = [name for used, maximum, name in totals if used > maximum]
    if enforce_aggregate_budget and exceeded:
        raise ValueError(f"generation replay exceeds its budget: {', '.join(exceeded)}")
    if resumable_proposal is not None:
        exhausted = [name for used, maximum, name in totals if used >= maximum]
        if exhausted or resumable_proposal > budget.proposal_cap:
            names = exhausted or ["proposal_cap"]
            raise ValueError(f"generation replay leaves no budget for a paid dispatch: {', '.join(names)}")


__all__ = ["revalidated_generation_record", "validate_generation_replay"]
