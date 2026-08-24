"""Typed, budgeted control-plane generation for kernel campaigns."""

from __future__ import annotations

import ast
import math
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from autocontext.harness.cost.calculator import CostCalculator
from autocontext.kernel_evolution.models import (
    KernelCandidate,
    StrictModel,
    canonical_digest,
    content_digest,
)
from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length", "incomplete", "content_filter"})
_TRANSIENT_ERROR_MARKERS = frozenset(
    {
        "rate limit",
        "rate_limit",
        "429",
        "timeout",
        "timed out",
        "server error",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "capacity",
        "connection",
        "temporarily unavailable",
    }
)


class KernelGenerationError(RuntimeError):
    """Base class for generation failures that must not reach the evaluator."""


class KernelGenerationCancelled(KernelGenerationError):
    """Raised when an operator stop is observed at a safe control-plane boundary."""


class KernelGenerationValidationError(KernelGenerationError):
    """Raised when a provider response is not an exact executable source artifact."""


class KernelGenerationBudgetExceeded(KernelGenerationError):
    """Raised before GPU work when the durable generation budget is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        result: KernelGenerationResult | None = None,
        failures: tuple[KernelGenerationFailure, ...] = (),
    ) -> None:
        super().__init__(message)
        self.result = result
        self.failures = failures


class KernelGenerationProviderError(KernelGenerationError):
    """Provider failure with all bounded retry evidence attached."""

    def __init__(self, message: str, *, failures: tuple[KernelGenerationFailure, ...]) -> None:
        super().__init__(message)
        self.failures = failures


class KernelGenerationUsage(StrictModel):
    """Normalized usage plus the provider's integer counters."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    provider_usage: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        if any(isinstance(value, bool) or value < 0 for value in self.provider_usage.values()):
            raise ValueError("provider usage values must be non-negative integers")
        minimum = self.input_tokens + self.output_tokens
        if self.total_tokens < minimum:
            raise ValueError("total_tokens cannot be smaller than input_tokens + output_tokens")
        return self


class KernelGenerationFailure(StrictModel):
    """One failed provider call or rejected response in a bounded retry sequence."""

    schema_version: Literal["autocontext.kernel-generation-failure/v1"] = (
        "autocontext.kernel-generation-failure/v1"
    )
    proposal_index: int = Field(ge=1)
    call_index: int = Field(ge=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    outcome: Literal["provider_error", "invalid_response", "budget_exceeded", "cancelled"]
    retryable: bool
    error_type: str = Field(min_length=1)
    error: str = Field(min_length=1, max_length=1_000)
    usage: KernelGenerationUsage = Field(default_factory=KernelGenerationUsage)
    cost_usd: FiniteFloat = Field(default=0.0, ge=0.0)
    cost_source: Literal["provider-reported", "estimated-model-pricing-v1"] = (
        "estimated-model-pricing-v1"
    )
    latency_seconds: FiniteFloat = Field(default=0.0, ge=0.0)
    retry_delay_seconds: FiniteFloat = Field(default=0.0, ge=0.0)
    occurred_at: str

    @property
    def failure_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelGenerationResult(StrictModel):
    """Exact generated source and complete non-secret generation provenance."""

    schema_version: Literal["autocontext.kernel-generation/v1"] = "autocontext.kernel-generation/v1"
    proposal_index: int = Field(ge=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    response_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source: str
    source_suffix: str
    entrypoint: str
    usage: KernelGenerationUsage
    cost_usd: FiniteFloat = Field(ge=0.0)
    cost_source: Literal["provider-reported", "estimated-model-pricing-v1", "not-billable"]
    latency_seconds: FiniteFloat = Field(ge=0.0)
    stop_reason: str | None = None
    retry_count: int = Field(ge=0)
    failures: tuple[KernelGenerationFailure, ...] = ()
    completed_at: str

    @model_validator(mode="after")
    def validate_source_identity(self) -> Self:
        candidate = KernelCandidate(
            source=self.source,
            source_suffix=self.source_suffix,
            entrypoint=self.entrypoint,
        )
        if self.response_digest != content_digest(self.source.encode("utf-8")):
            raise ValueError("response digest must bind the exact source response bytes")
        if self.source_digest != candidate.source_digest or self.artifact_digest != candidate.artifact_digest:
            raise ValueError("generation source identity does not match its exact source bytes and ABI")
        if self.retry_count != len(self.failures):
            raise ValueError("retry_count must equal the number of preceding failed calls")
        if any(failure.proposal_index != self.proposal_index for failure in self.failures):
            raise ValueError("generation failures must belong to the same proposal")
        return self

    @property
    def receipt_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


KernelGenerateFn = Callable[[str, int], str | KernelGenerationResult]


class KernelGenerationBudget(StrictModel):
    """Deterministic campaign-wide provider budget owned by the control plane."""

    schema_version: Literal["autocontext.kernel-generation-budget/v1"] = (
        "autocontext.kernel-generation-budget/v1"
    )
    proposal_cap: int = Field(default=10, ge=1)
    max_retries_per_proposal: int = Field(default=2, ge=0)
    max_output_tokens_per_call: int = Field(default=8_192, ge=1)
    max_total_input_tokens: int = Field(default=200_000, ge=1)
    max_total_output_tokens: int = Field(default=100_000, ge=1)
    max_total_tokens: int = Field(default=300_000, ge=1)
    max_cost_usd: FiniteFloat = Field(default=100.0, gt=0.0)
    max_wall_seconds: FiniteFloat = Field(default=86_400.0, gt=0.0)
    retry_backoff_seconds: FiniteFloat = Field(default=1.0, ge=0.0)
    max_source_bytes: int = Field(default=1_000_000, ge=1)

    @model_validator(mode="after")
    def validate_token_ceiling(self) -> Self:
        if self.max_total_tokens < max(self.max_total_input_tokens, self.max_total_output_tokens):
            raise ValueError("max_total_tokens cannot be smaller than either directional token budget")
        return self

    @property
    def budget_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelGenerationBudgetState(StrictModel):
    completed_proposals: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: FiniteFloat = Field(default=0.0, ge=0.0)
    wall_seconds: FiniteFloat = Field(default=0.0, ge=0.0)

    @classmethod
    def from_results(cls, results: Iterable[KernelGenerationResult]) -> KernelGenerationBudgetState:
        return cls.from_activity(results)

    @classmethod
    def from_activity(
        cls,
        results: Iterable[KernelGenerationResult],
        terminal_failures: Iterable[KernelGenerationFailure] = (),
    ) -> KernelGenerationBudgetState:
        completed = 0
        input_tokens = output_tokens = total_tokens = 0
        cost_usd = wall_seconds = 0.0
        for result in results:
            completed += 1
            records: tuple[KernelGenerationFailure | KernelGenerationResult, ...] = (*result.failures, result)
            for record in records:
                input_tokens += record.usage.input_tokens
                output_tokens += record.usage.output_tokens
                total_tokens += record.usage.total_tokens
                cost_usd += float(record.cost_usd)
                wall_seconds += float(record.latency_seconds)
                if isinstance(record, KernelGenerationFailure):
                    wall_seconds += float(record.retry_delay_seconds)
        for failure in terminal_failures:
            input_tokens += failure.usage.input_tokens
            output_tokens += failure.usage.output_tokens
            total_tokens += failure.usage.total_tokens
            cost_usd += float(failure.cost_usd)
            wall_seconds += float(failure.latency_seconds) + float(failure.retry_delay_seconds)
        return cls(
            completed_proposals=completed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            wall_seconds=wall_seconds,
        )


def normalized_generation_usage(usage: dict[str, int] | None) -> KernelGenerationUsage:
    raw = dict(usage or {})
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"provider usage field {key!r} must be a non-negative integer")
    input_tokens = int(raw.get("input_tokens", raw.get("prompt_tokens", 0)))
    output_tokens = int(raw.get("output_tokens", raw.get("completion_tokens", 0)))
    total_tokens = int(raw.get("total_tokens", input_tokens + output_tokens))
    return KernelGenerationUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider_usage=raw,
    )


def validate_kernel_source(
    source: str,
    *,
    source_suffix: str,
    entrypoint: str,
    stop_reason: str | None,
    max_source_bytes: int,
) -> str:
    """Return exact bytes only when the response is a complete source artifact."""
    if stop_reason is not None and stop_reason.lower().strip() in _TRUNCATED_STOP_REASONS:
        raise KernelGenerationValidationError(f"provider response was truncated (stop_reason={stop_reason})")
    if not source.strip():
        raise KernelGenerationValidationError("provider returned empty kernel source")
    if "```" in source:
        raise KernelGenerationValidationError("provider returned Markdown fences instead of exact source")
    encoded = source.encode("utf-8")
    if len(encoded) > max_source_bytes:
        raise KernelGenerationValidationError(
            f"provider response exceeds the {max_source_bytes}-byte source limit"
        )
    if source_suffix == ".py":
        try:
            tree = ast.parse(source, filename="<generated-kernel>", mode="exec")
        except SyntaxError as exc:
            raise KernelGenerationValidationError(
                f"provider returned malformed Python source: {exc.msg} at line {exc.lineno}"
            ) from exc
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if entrypoint not in definitions:
            raise KernelGenerationValidationError(
                f"provider response does not define required top-level entrypoint {entrypoint!r}"
            )
    return source


def build_generation_result(
    *,
    proposal_index: int,
    provider: str,
    model: str,
    system_prompt: str,
    prompt: str,
    completion: CompletionResult,
    source_suffix: str,
    entrypoint: str,
    latency_seconds: float,
    retry_failures: tuple[KernelGenerationFailure, ...] = (),
    max_source_bytes: int = 1_000_000,
    cost_calculator: CostCalculator | None = None,
    completed_at: str | None = None,
) -> KernelGenerationResult:
    source = validate_kernel_source(
        completion.text,
        source_suffix=source_suffix,
        entrypoint=entrypoint,
        stop_reason=completion.stop_reason,
        max_source_bytes=max_source_bytes,
    )
    usage = normalized_generation_usage(completion.usage)
    actual_model = (completion.model or model).strip()
    if not actual_model:
        raise KernelGenerationValidationError("provider did not identify the generation model")
    if completion.cost_usd is None:
        estimate = (cost_calculator or CostCalculator()).calculate(
            actual_model,
            usage.input_tokens,
            usage.output_tokens,
        )
        cost_usd = estimate.total_cost
        cost_source: Literal["provider-reported", "estimated-model-pricing-v1", "not-billable"] = (
            "estimated-model-pricing-v1"
        )
    else:
        cost_usd = float(completion.cost_usd)
        cost_source = "provider-reported"
    candidate = KernelCandidate(source=source, source_suffix=source_suffix, entrypoint=entrypoint)
    return KernelGenerationResult(
        proposal_index=proposal_index,
        provider=provider,
        model=actual_model,
        system_prompt_digest=content_digest(system_prompt.encode("utf-8")),
        prompt_digest=content_digest(prompt.encode("utf-8")),
        response_digest=content_digest(source.encode("utf-8")),
        source_digest=candidate.source_digest,
        artifact_digest=candidate.artifact_digest,
        source=source,
        source_suffix=source_suffix,
        entrypoint=entrypoint,
        usage=usage,
        cost_usd=cost_usd,
        cost_source=cost_source,
        latency_seconds=latency_seconds,
        stop_reason=completion.stop_reason,
        retry_count=len(retry_failures),
        failures=retry_failures,
        completed_at=completed_at or datetime.now(UTC).isoformat(),
    )


class ProviderKernelGenerator:
    """Provider-registry-compatible generator with explicit bounded retries."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        provider_id: str,
        model: str | None,
        budget: KernelGenerationBudget,
        transport_identity: str | None = None,
        source_suffix: str = ".py",
        entrypoint: str = "kernel_fn",
        system_prompt: str = (
            "You are a kernel implementation worker. Return only the complete executable source artifact requested "
            "by the user. Do not use Markdown fences or explanatory prose."
        ),
        temperature: float = 0.0,
        cancellation_requested: Callable[[], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must not be empty")
        if not math.isfinite(temperature) or temperature < 0:
            raise ValueError("temperature must be non-negative and finite")
        self._provider = provider
        self.provider_id = provider_id.strip()
        self.transport_identity = (transport_identity or self.provider_id).strip()
        if not self.transport_identity:
            raise ValueError("transport_identity must not be empty")
        self.model = (model or provider.default_model()).strip()
        if not self.model:
            raise ValueError("generation model must not be empty")
        self.budget = budget
        self.source_suffix = source_suffix
        self.entrypoint = entrypoint
        self.system_prompt = system_prompt
        self.temperature = temperature
        self._cancelled = cancellation_requested or (lambda: False)
        self._monotonic = monotonic
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._history: list[KernelGenerationResult] = []

    def restore(self, history: Iterable[KernelGenerationResult]) -> None:
        restored = list(history)
        if [item.proposal_index for item in restored] != list(range(1, len(restored) + 1)):
            raise ValueError("generation history must be contiguous from proposal one")
        self._history = restored

    @property
    def budget_state(self) -> KernelGenerationBudgetState:
        return KernelGenerationBudgetState.from_results(self._history)

    def __call__(self, prompt: str, generation: int) -> KernelGenerationResult:
        proposal_index = generation + 1
        state = self.budget_state
        self._require_start_budget(proposal_index, state)
        failures: list[KernelGenerationFailure] = []
        for call_index in range(1, self.budget.max_retries_per_proposal + 2):
            if self._cancelled():
                raise KernelGenerationCancelled("kernel campaign stop requested before provider dispatch")
            remaining_output = min(
                self.budget.max_output_tokens_per_call,
                self.budget.max_total_output_tokens - state.output_tokens,
                self.budget.max_total_tokens - state.total_tokens,
            )
            if remaining_output < 1:
                raise KernelGenerationBudgetExceeded("kernel generation token budget is exhausted")
            started = self._monotonic()
            completion: CompletionResult | None = None
            latency: float | None = None
            try:
                completion = self._provider.complete(
                    self.system_prompt,
                    prompt,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=remaining_output,
                )
                latency = max(0.0, self._monotonic() - started)
                result = build_generation_result(
                    proposal_index=proposal_index,
                    provider=self.provider_id,
                    model=self.model,
                    system_prompt=self.system_prompt,
                    prompt=prompt,
                    completion=completion,
                    source_suffix=self.source_suffix,
                    entrypoint=self.entrypoint,
                    latency_seconds=latency,
                    retry_failures=tuple(failures),
                    max_source_bytes=self.budget.max_source_bytes,
                    completed_at=self._now().isoformat(),
                )
            except (ProviderError, KernelGenerationValidationError, ValueError) as exc:
                if latency is None:
                    latency = max(0.0, self._monotonic() - started)
                raw_usage = (
                    exc.usage
                    if isinstance(exc, ProviderError)
                    else completion.usage
                    if completion is not None
                    else {}
                )
                usage_valid = True
                try:
                    usage = normalized_generation_usage(raw_usage)
                except ValueError as usage_exc:
                    usage = KernelGenerationUsage()
                    usage_valid = False
                    exc = KernelGenerationValidationError(
                        f"provider returned invalid usage metadata: {usage_exc}"
                    )
                retryable = (
                    self._is_transient(exc)
                    if isinstance(exc, ProviderError)
                    else usage_valid
                )
                has_retry = call_index <= self.budget.max_retries_per_proposal and retryable
                delay = (
                    float(self.budget.retry_backoff_seconds) * (2 ** (call_index - 1))
                    if has_retry
                    else 0.0
                )
                failure = self._failure(
                    proposal_index=proposal_index,
                    call_index=call_index,
                    model=(completion.model or self.model) if completion is not None else self.model,
                    exc=exc,
                    outcome="provider_error" if isinstance(exc, ProviderError) else "invalid_response",
                    retryable=retryable,
                    usage=usage,
                    latency=latency,
                    reported_cost_usd=completion.cost_usd if completion is not None else None,
                )
                failures.append(failure)
                state = self._state_with_failures(state, (failure,))
                self._require_within_budget(state, failures=tuple(failures))
                if not has_retry:
                    raise KernelGenerationProviderError(
                        f"kernel generation failed after {call_index} bounded call(s): {failure.error}",
                        failures=tuple(failures),
                    ) from exc
                if state.wall_seconds + delay > float(self.budget.max_wall_seconds):
                    raise KernelGenerationBudgetExceeded(
                        "kernel generation wall-clock budget cannot admit the next retry",
                        failures=tuple(failures),
                    ) from exc
                if delay:
                    failure = failure.model_copy(update={"retry_delay_seconds": delay})
                    failures[-1] = failure
                    state = state.model_copy(
                        update={"wall_seconds": float(state.wall_seconds) + delay}
                    )
                    self._sleep(delay)
                continue

            combined = self._state_with_result(state, result)
            self._require_within_budget(combined, result=result, failures=tuple(failures))
            self._history.append(result)
            return result
        raise AssertionError("bounded provider loop did not terminate")

    def _require_start_budget(self, proposal_index: int, state: KernelGenerationBudgetState) -> None:
        if proposal_index != state.completed_proposals + 1:
            raise KernelGenerationBudgetExceeded(
                "proposal index does not follow the restored generation history"
            )
        if proposal_index > self.budget.proposal_cap:
            raise KernelGenerationBudgetExceeded("kernel generation proposal cap is exhausted")
        self._require_within_budget(state)

    def _require_within_budget(
        self,
        state: KernelGenerationBudgetState,
        *,
        result: KernelGenerationResult | None = None,
        failures: tuple[KernelGenerationFailure, ...] = (),
    ) -> None:
        exceeded = []
        if state.input_tokens > self.budget.max_total_input_tokens:
            exceeded.append("input_tokens")
        if state.output_tokens > self.budget.max_total_output_tokens:
            exceeded.append("output_tokens")
        if state.total_tokens > self.budget.max_total_tokens:
            exceeded.append("total_tokens")
        if float(state.cost_usd) > float(self.budget.max_cost_usd):
            exceeded.append("cost_usd")
        if float(state.wall_seconds) > float(self.budget.max_wall_seconds):
            exceeded.append("wall_seconds")
        if exceeded:
            raise KernelGenerationBudgetExceeded(
                f"kernel generation budget exceeded: {', '.join(exceeded)}",
                result=result,
                failures=failures,
            )

    def _failure(
        self,
        *,
        proposal_index: int,
        call_index: int,
        model: str,
        exc: Exception,
        outcome: Literal["provider_error", "invalid_response"],
        retryable: bool,
        usage: KernelGenerationUsage,
        latency: float,
        reported_cost_usd: float | None,
    ) -> KernelGenerationFailure:
        actual_model = model.strip() or self.model
        estimated = CostCalculator().calculate(actual_model, usage.input_tokens, usage.output_tokens)
        reported_cost = float(reported_cost_usd) if reported_cost_usd is not None else None
        valid_reported_cost = (
            reported_cost is not None
            and math.isfinite(reported_cost)
            and reported_cost >= 0.0
        )
        accounted_cost = reported_cost if valid_reported_cost and reported_cost is not None else estimated.total_cost
        return KernelGenerationFailure(
            proposal_index=proposal_index,
            call_index=call_index,
            provider=self.provider_id,
            model=actual_model,
            outcome=outcome,
            retryable=retryable,
            error_type=type(exc).__name__,
            error=str(exc)[:1_000] or type(exc).__name__,
            usage=usage,
            cost_usd=accounted_cost,
            cost_source=("provider-reported" if valid_reported_cost else "estimated-model-pricing-v1"),
            latency_seconds=latency,
            occurred_at=self._now().isoformat(),
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)

    @staticmethod
    def _state_with_failures(
        state: KernelGenerationBudgetState,
        failures: tuple[KernelGenerationFailure, ...],
    ) -> KernelGenerationBudgetState:
        return state.model_copy(
            update={
                "input_tokens": state.input_tokens + sum(item.usage.input_tokens for item in failures),
                "output_tokens": state.output_tokens + sum(item.usage.output_tokens for item in failures),
                "total_tokens": state.total_tokens + sum(item.usage.total_tokens for item in failures),
                "cost_usd": float(state.cost_usd) + sum(float(item.cost_usd) for item in failures),
                "wall_seconds": float(state.wall_seconds)
                + sum(
                    float(item.latency_seconds) + float(item.retry_delay_seconds)
                    for item in failures
                ),
            }
        )

    @staticmethod
    def _state_with_result(
        state: KernelGenerationBudgetState,
        result: KernelGenerationResult,
    ) -> KernelGenerationBudgetState:
        return state.model_copy(
            update={
                "completed_proposals": state.completed_proposals + 1,
                "input_tokens": state.input_tokens + result.usage.input_tokens,
                "output_tokens": state.output_tokens + result.usage.output_tokens,
                "total_tokens": state.total_tokens + result.usage.total_tokens,
                "cost_usd": float(state.cost_usd) + float(result.cost_usd),
                "wall_seconds": float(state.wall_seconds) + float(result.latency_seconds),
            }
        )


__all__ = [
    "KernelGenerationBudget",
    "KernelGenerationBudgetExceeded",
    "KernelGenerationBudgetState",
    "KernelGenerationCancelled",
    "KernelGenerationError",
    "KernelGenerationFailure",
    "KernelGenerateFn",
    "KernelGenerationProviderError",
    "KernelGenerationResult",
    "KernelGenerationUsage",
    "KernelGenerationValidationError",
    "ProviderKernelGenerator",
    "build_generation_result",
    "normalized_generation_usage",
    "validate_kernel_source",
]
