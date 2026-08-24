from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from test_kernel_evolution import FakeBenchmarkRunner, _evaluator

from autocontext.kernel_evolution import (
    KernelCampaignAmbiguousExecution,
    KernelCampaignJournal,
    KernelCampaignJournalError,
    KernelCandidate,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelGenerationBudget,
    KernelGenerationBudgetExceeded,
    KernelGenerationCancelled,
    KernelGenerationFailure,
    KernelGenerationProviderError,
    KernelGenerationResult,
    KernelGenerationUsage,
    ProviderKernelGenerator,
    content_digest,
    read_kernel_campaign_status,
    request_kernel_campaign_stop,
)
from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError
from autocontext.providers.retry import RetryProvider

VALID_SOURCE = "class ModelNew:\n    pass\n"
VALID_SOURCE_TWO = "class ModelNew:\n    version = 2\n"


class MockProvider(LLMProvider):
    supports_single_dispatch = True

    def __init__(
        self,
        responses: list[CompletionResult | Exception],
        *,
        credential: str | None = None,
        after_complete: Callable[[], None] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.credential = credential
        self.after_complete = after_complete
        self.calls: list[tuple[str, str, str | None, float, int]] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema=None,
    ) -> CompletionResult:
        del output_schema
        self.calls.append((system_prompt, user_prompt, model, temperature, max_tokens))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if self.after_complete is not None:
            self.after_complete()
        return response

    def default_model(self) -> str:
        return "mock-kernel-model"


class SlateGenerator:
    def __init__(
        self,
        sources: list[str],
        *,
        after_generate: Callable[[int], None] | None = None,
        interrupt: bool = False,
    ) -> None:
        self.sources = sources
        self.after_generate = after_generate
        self.interrupt = interrupt
        self.calls: list[int] = []

    def __call__(self, _prompt: str, generation: int) -> str:
        self.calls.append(generation)
        if self.interrupt:
            raise KeyboardInterrupt
        source = self.sources[generation]
        if self.after_generate is not None:
            self.after_generate(generation)
        return source


class ResumableClaimGenerator(SlateGenerator):
    supports_claim_resume = True

    def __call__(self, _prompt: str, generation: int) -> str:
        self.calls.append(generation)
        if self.interrupt:
            raise KernelGenerationCancelled("safe external generator stop")
        return self.sources[generation]


class IncompleteCallFenceGenerator(ResumableClaimGenerator):
    supports_durable_call_fence = True

    def __init__(self, sources: list[str], *, interrupt: bool = False) -> None:
        super().__init__(sources, interrupt=interrupt)
        self._call_observer: Callable[[int, int], None] | None = None

    def set_call_observer(self, observer: Callable[[int, int], None]) -> None:
        self._call_observer = observer

    def restore_pending_failures(self, proposal_index: int, failures: object) -> None:
        del proposal_index, failures

    def __call__(self, prompt: str, generation: int) -> str:
        if self._call_observer is not None:
            self._call_observer(generation + 1, 1)
        return super().__call__(prompt, generation)


class PaidReceiptGenerator:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, prompt: str, generation: int) -> KernelGenerationResult:
        self.calls.append(generation)
        candidate = KernelCandidate(source=VALID_SOURCE, source_suffix=".py", entrypoint="ModelNew")
        return KernelGenerationResult(
            proposal_index=generation + 1,
            provider="typed-test",
            model="typed-test",
            system_prompt_digest=content_digest("typed-test-system"),
            prompt_digest=content_digest(prompt),
            response_digest=content_digest(VALID_SOURCE),
            source_digest=candidate.source_digest,
            artifact_digest=candidate.artifact_digest,
            source=VALID_SOURCE,
            source_suffix=".py",
            entrypoint="ModelNew",
            usage=KernelGenerationUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            cost_usd=0.25,
            cost_source="provider-reported",
            latency_seconds=0.1,
            retry_count=0,
            completed_at=datetime.now(UTC).isoformat(),
        )


class StoppingBenchmarkRunner(FakeBenchmarkRunner):
    def __init__(self) -> None:
        super().__init__()
        self.after_candidate: Callable[[], None] | None = None

    def run(self, candidate, incumbent, *, timeout_seconds):
        result = super().run(candidate, incumbent, timeout_seconds=timeout_seconds)
        if candidate.artifact_digest != incumbent.artifact_digest and self.after_candidate is not None:
            self.after_candidate()
        return result


class InterruptingBenchmarkRunner(FakeBenchmarkRunner):
    def __init__(self, *, interrupt: bool) -> None:
        super().__init__()
        self.interrupt = interrupt

    def manifest(self):
        return FakeBenchmarkRunner.manifest(self)

    def run(self, candidate, incumbent, *, timeout_seconds):
        if self.interrupt and candidate.artifact_digest != incumbent.artifact_digest:
            raise KeyboardInterrupt
        return super().run(candidate, incumbent, timeout_seconds=timeout_seconds)


def _runner(
    root: Path,
    generator,
    benchmark: FakeBenchmarkRunner,
    *,
    run_id: str,
    resume: bool = False,
    budget: KernelGenerationBudget | None = None,
) -> KernelEvolutionRunner:
    return KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="kernelbench-level1-problem1",
            task_prompt="Improve ModelNew and return exact source.",
            baseline_source="baseline",
            min_relative_improvement=0.05,
            target_reference_speedup=3.0,
        ),
        generator,
        _evaluator(benchmark),
        root,
        run_id=run_id,
        generation_budget=budget or KernelGenerationBudget(proposal_cap=2),
        resume=resume,
    )


def test_provider_generator_persists_exact_provenance_and_retry_accounting() -> None:
    provider = MockProvider(
        [
            CompletionResult(
                text="```python\nclass ModelNew: pass\n```",
                model="mock-kernel-model",
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                cost_usd=0.02,
            ),
            CompletionResult(
                text=VALID_SOURCE,
                model="mock-kernel-model-v2",
                usage={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
                cost_usd=0.03,
                stop_reason="stop",
            ),
        ]
    )
    clock = iter((0.0, 0.25, 1.0, 1.5))
    sleeps: list[float] = []
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock-kernel-model",
        budget=KernelGenerationBudget(
            proposal_cap=1,
            max_retries_per_proposal=1,
            retry_backoff_seconds=0.5,
        ),
        entrypoint="ModelNew",
        monotonic=lambda: next(clock),
        sleep=sleeps.append,
    )

    result = generator("exact prompt", 0)

    assert result.source == VALID_SOURCE
    assert result.model == "mock-kernel-model-v2"
    assert result.prompt_digest == content_digest("exact prompt")
    assert result.response_digest == content_digest(VALID_SOURCE)
    assert result.retry_count == 1
    assert result.failures[0].usage.total_tokens == 15
    assert result.failures[0].cost_usd == pytest.approx(0.02)
    assert result.failures[0].cost_source == "provider-reported"
    assert result.failures[0].latency_seconds == pytest.approx(0.25)
    assert result.failures[0].retry_delay_seconds == pytest.approx(0.5)
    assert result.usage.total_tokens == 28
    assert result.cost_usd == pytest.approx(0.03)
    assert result.latency_seconds == pytest.approx(0.5)
    assert generator.budget_state.total_tokens == 43
    assert generator.budget_state.cost_usd == pytest.approx(0.05)
    assert generator.budget_state.wall_seconds == pytest.approx(1.25)
    assert sleeps == [0.5]
    assert provider.calls[0][-1] == 8192


def test_non_transient_provider_failure_does_not_consume_retry_capacity() -> None:
    provider = MockProvider([ProviderError("authentication failed", usage={"input_tokens": 3})])
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock",
        budget=KernelGenerationBudget(proposal_cap=1, max_retries_per_proposal=3),
        entrypoint="ModelNew",
    )

    with pytest.raises(KernelGenerationProviderError) as raised:
        generator("exact prompt", 0)

    assert len(provider.calls) == 1
    assert raised.value.failures[0].outcome == "provider_error"
    assert not raised.value.failures[0].retryable
    assert raised.value.failures[0].usage.input_tokens == 3


def test_provider_generator_rejects_hidden_transport_retries() -> None:
    provider = RetryProvider(MockProvider([]), max_retries=1)

    with pytest.raises(ValueError, match="single-dispatch provider"):
        ProviderKernelGenerator(
            provider,
            provider_id="mock",
            model="mock",
            budget=KernelGenerationBudget(proposal_cap=1),
        )


@pytest.mark.parametrize("cost_usd", [None, 0.25])
def test_success_without_directional_usage_fails_closed(cost_usd: float | None) -> None:
    provider = MockProvider(
        [CompletionResult(text=VALID_SOURCE, model="paid-model", cost_usd=cost_usd)]
    )
    generator = ProviderKernelGenerator(
        provider,
        provider_id="paid",
        model="paid-model",
        budget=KernelGenerationBudget(proposal_cap=1, max_retries_per_proposal=3),
        entrypoint="ModelNew",
    )

    with pytest.raises(KernelGenerationProviderError) as raised:
        generator("exact prompt", 0)

    assert len(provider.calls) == 1
    assert not raised.value.failures[0].retryable
    assert "lacks trustworthy directional token usage" in raised.value.failures[0].error


def test_invalid_source_without_usage_cannot_consume_an_unaccounted_retry() -> None:
    provider = MockProvider(
        [
            CompletionResult(text="not executable", model="paid", cost_usd=0.25),
            CompletionResult(
                text=VALID_SOURCE,
                model="paid",
                usage={"input_tokens": 2, "output_tokens": 3},
                cost_usd=0.25,
            ),
        ]
    )
    generator = ProviderKernelGenerator(
        provider,
        provider_id="paid",
        model="paid",
        budget=KernelGenerationBudget(proposal_cap=1, max_retries_per_proposal=1),
        entrypoint="ModelNew",
    )

    with pytest.raises(KernelGenerationProviderError) as raised:
        generator("exact prompt", 0)

    assert len(provider.calls) == 1
    assert not raised.value.failures[0].retryable
    assert "lacks trustworthy directional token usage" in raised.value.failures[0].error


def test_failure_receipt_is_redacted_before_observation_or_exception() -> None:
    secrets = ("sk-secret-123", "api-secret", "query-secret", "url-password")
    observed: list[KernelGenerationFailure] = []
    provider = MockProvider(
        [
            ProviderError(
                "timeout; Authorization: Bearer sk-secret-123; API_KEY=api-secret; "
                "https://user:url-password@example.test/path?token=query-secret"
            )
        ]
    )
    generator = ProviderKernelGenerator(
        provider,
        provider_id="paid",
        model="paid-model",
        budget=KernelGenerationBudget(proposal_cap=1, max_retries_per_proposal=0),
        entrypoint="ModelNew",
        failure_observer=observed.append,
    )

    with pytest.raises(KernelGenerationProviderError) as raised:
        generator("exact prompt", 0)

    persisted = json.dumps(raised.value.failures[0].model_dump(mode="json"))
    assert all(secret not in persisted for secret in secrets)
    assert "REDACTED" in persisted
    assert observed == [raised.value.failures[0]]


def test_failure_is_observed_before_backoff_and_survives_cancellation() -> None:
    cancelled = False
    events: list[tuple[str, object]] = []

    def sleep(delay: float) -> None:
        nonlocal cancelled
        events.append(("sleep", delay))
        cancelled = True

    provider = MockProvider([ProviderError("503 overloaded", usage={"input_tokens": 2})])
    clock = iter((0.0, 0.1))
    generator = ProviderKernelGenerator(
        provider,
        provider_id="paid",
        model="paid-model",
        budget=KernelGenerationBudget(
            proposal_cap=1,
            max_retries_per_proposal=1,
            retry_backoff_seconds=0.25,
        ),
        entrypoint="ModelNew",
        cancellation_requested=lambda: cancelled,
        call_observer=lambda proposal, call: events.append(("call", (proposal, call))),
        failure_observer=lambda failure: events.append(("failure", failure)),
        monotonic=lambda: next(clock),
        sleep=sleep,
    )

    with pytest.raises(KernelGenerationCancelled) as raised:
        generator("exact prompt", 0)

    failure = raised.value.failures[0]
    assert failure.retry_delay_seconds == pytest.approx(0.25)
    assert events == [("call", (1, 1)), ("failure", failure), ("sleep", 0.25)]
    assert generator.budget_state.input_tokens == 2
    assert generator.budget_state.wall_seconds == pytest.approx(0.35)


def test_pending_failure_restore_resumes_at_next_physical_call() -> None:
    receipts: list[KernelGenerationFailure] = []

    def crash_after_receipt(failure: KernelGenerationFailure) -> None:
        receipts.append(failure)
        raise RuntimeError("simulated crash after durable failure receipt")

    first_provider = MockProvider([ProviderError("503 overloaded", usage={"input_tokens": 2})])
    first = ProviderKernelGenerator(
        first_provider,
        provider_id="paid",
        model="paid-model",
        budget=KernelGenerationBudget(
            proposal_cap=1,
            max_retries_per_proposal=1,
            retry_backoff_seconds=0.25,
        ),
        entrypoint="ModelNew",
        failure_observer=crash_after_receipt,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        first("exact prompt", 0)
    assert len(first_provider.calls) == 1
    assert receipts[0].retry_delay_seconds == pytest.approx(0.25)

    claims: list[tuple[int, int]] = []
    resumed_provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE,
                model="paid-model",
                usage={"input_tokens": 3, "output_tokens": 4},
                cost_usd=0.01,
            )
        ]
    )
    resumed = ProviderKernelGenerator(
        resumed_provider,
        provider_id="paid",
        model="paid-model",
        budget=first.budget,
        entrypoint="ModelNew",
        call_observer=lambda proposal, call: claims.append((proposal, call)),
    )
    resumed.restore_pending_failures(1, receipts)

    result = resumed("exact prompt", 0)

    assert claims == [(1, 2)]
    assert result.failures == tuple(receipts)
    assert result.retry_count == 1
    assert resumed.budget_state.input_tokens == 5


def test_token_budget_exit_retains_accumulated_failures() -> None:
    provider = MockProvider([ProviderError("503 overloaded", usage={"output_tokens": 1})])
    generator = ProviderKernelGenerator(
        provider,
        provider_id="paid",
        model="paid-model",
        budget=KernelGenerationBudget(
            proposal_cap=1,
            max_retries_per_proposal=1,
            max_total_input_tokens=1,
            max_total_output_tokens=1,
            max_total_tokens=1,
            retry_backoff_seconds=0,
        ),
        entrypoint="ModelNew",
    )

    with pytest.raises(KernelGenerationBudgetExceeded) as raised:
        generator("exact prompt", 0)

    assert len(raised.value.failures) == 1
    assert raised.value.failures[0].usage.output_tokens == 1
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    ("text", "stop_reason"),
    [
        ("", None),
        ("```python\nclass ModelNew: pass\n```", None),
        ("Here is the improved kernel.", None),
        ("class ModelNew(", None),
        (VALID_SOURCE, "max_tokens"),
    ],
)
def test_invalid_provider_output_never_reaches_gpu(
    tmp_path: Path,
    text: str,
    stop_reason: str | None,
) -> None:
    provider = MockProvider(
        [CompletionResult(text=text, model="mock", stop_reason=stop_reason, cost_usd=0.01)]
    )
    budget = KernelGenerationBudget(proposal_cap=2, max_retries_per_proposal=0)
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    benchmark = FakeBenchmarkRunner()
    runner = _runner(tmp_path, generator, benchmark, run_id="invalid-output", budget=budget)

    with pytest.raises(KernelGenerationProviderError):
        runner.run(proposals=1)

    assert [call[0] for call in benchmark.calls] == ["baseline"]
    failure = json.loads(
        (runner.run_dir / "generation" / "proposals" / "000001" / "failure.json").read_text()
    )
    assert failure["outcome"] == "provider_error"
    assert failure["failures"][0]["outcome"] == "invalid_response"
    assert read_kernel_campaign_status(tmp_path, runner.run_id).generation_budget_state.cost_usd == 0.01


def test_paid_result_over_budget_is_durable_but_not_evaluated(tmp_path: Path) -> None:
    provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE,
                model="mock",
                usage={"input_tokens": 2, "output_tokens": 3},
                cost_usd=0.25,
            )
        ]
    )
    budget = KernelGenerationBudget(
        proposal_cap=2,
        max_retries_per_proposal=0,
        max_cost_usd=0.1,
    )
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    benchmark = FakeBenchmarkRunner()
    runner = _runner(tmp_path, generator, benchmark, run_id="cost-budget", budget=budget)

    with pytest.raises(KernelGenerationBudgetExceeded):
        runner.run(proposals=1)

    assert [call[0] for call in benchmark.calls] == ["baseline"]
    journal = KernelCampaignJournal(runner.run_dir, runner.run_id)
    assert journal.read_generation_result(1) is not None
    assert read_kernel_campaign_status(tmp_path, runner.run_id).generation_budget_state.cost_usd == 0.25


def test_provider_credential_never_enters_campaign_artifacts(tmp_path: Path) -> None:
    secret = "provider-secret-must-remain-control-plane-only"
    provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE,
                model="mock",
                usage={"input_tokens": 2, "output_tokens": 3},
                cost_usd=0.01,
            )
        ],
        credential=secret,
    )
    budget = KernelGenerationBudget(proposal_cap=2, max_retries_per_proposal=0)
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    benchmark = FakeBenchmarkRunner()
    benchmark.latencies[VALID_SOURCE.strip()] = 90.0
    runner = _runner(tmp_path, generator, benchmark, run_id="credential-boundary", budget=budget)

    runner.run(proposals=1)

    persisted = b"\n".join(
        path.read_bytes()
        for path in runner.run_dir.rglob("*")
        if path.is_file()
    )
    assert secret.encode() not in persisted
    prompt_artifacts = tuple((runner.run_dir / "prompts").glob("*.md"))
    assert len(prompt_artifacts) == 2


def test_stop_after_callable_receipt_resumes_without_duplicate_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resume-receipt"
    first = SlateGenerator(
        ["winner", "tiny-gain"],
        after_generate=lambda _generation: request_kernel_campaign_stop(root, run_id),
    )
    first_benchmark = FakeBenchmarkRunner()
    initial = _runner(root, first, first_benchmark, run_id=run_id)

    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=2)

    assert first.calls == [0]
    assert [call[0] for call in first_benchmark.calls] == ["baseline"]
    stopped = read_kernel_campaign_status(root, run_id)
    assert stopped.status == "cancelled"
    assert stopped.proposals_generated == 1
    assert stopped.stop_requested
    assert stopped.can_resume

    invalid_resume = _runner(
        root,
        SlateGenerator(["winner", "tiny-gain"]),
        FakeBenchmarkRunner(),
        run_id=run_id,
        resume=True,
        budget=KernelGenerationBudget(proposal_cap=2, max_cost_usd=50.0),
    )
    with pytest.raises(ValueError, match="generation budget changed"):
        invalid_resume.run(proposals=2)
    assert read_kernel_campaign_status(root, run_id).stop_requested

    resumed_generator = SlateGenerator(["winner", "tiny-gain"])
    resumed_benchmark = FakeBenchmarkRunner()
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    )
    result = resumed.run(proposals=2)

    assert resumed_generator.calls == [1]
    assert [call[0] for call in resumed_benchmark.calls] == ["winner", "tiny-gain"]
    assert len(result.attempts) == 3
    assert len({attempt.attempt_id for attempt in result.attempts}) == 3
    assert not read_kernel_campaign_status(root, run_id).stop_requested


def test_provider_generator_restores_paid_history_and_only_calls_next_proposal(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resume-provider-history"
    budget = KernelGenerationBudget(proposal_cap=2, max_retries_per_proposal=0)
    first_provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE,
                model="mock",
                usage={"input_tokens": 2, "output_tokens": 3},
                cost_usd=0.05,
            )
        ],
        after_complete=lambda: request_kernel_campaign_stop(root, run_id),
    )
    first_generator = ProviderKernelGenerator(
        first_provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    first_benchmark = FakeBenchmarkRunner()
    first_benchmark.latencies[VALID_SOURCE.strip()] = 90.0
    first_benchmark.latencies[VALID_SOURCE_TWO.strip()] = 95.0
    initial = _runner(
        root,
        first_generator,
        first_benchmark,
        run_id=run_id,
        budget=budget,
    )

    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=2)

    assert len(first_provider.calls) == 1
    assert [call[0] for call in first_benchmark.calls] == ["baseline"]

    resumed_provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE_TWO,
                model="mock",
                usage={"input_tokens": 3, "output_tokens": 4},
                cost_usd=0.07,
            )
        ]
    )
    resumed_generator = ProviderKernelGenerator(
        resumed_provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    resumed_benchmark = FakeBenchmarkRunner()
    resumed_benchmark.latencies[VALID_SOURCE.strip()] = 90.0
    resumed_benchmark.latencies[VALID_SOURCE_TWO.strip()] = 95.0
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
        budget=budget,
    )
    result = resumed.run(proposals=2)

    assert len(resumed_provider.calls) == 1
    assert resumed_generator.budget_state.completed_proposals == 2
    assert resumed_generator.budget_state.cost_usd == pytest.approx(0.12)
    assert result.champion_source == VALID_SOURCE


def test_stop_after_attempt_resumes_at_next_proposal_without_champion_drift(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resume-next"
    generator = SlateGenerator(["winner", "tiny-gain"])
    benchmark = StoppingBenchmarkRunner()
    initial = _runner(root, generator, benchmark, run_id=run_id)
    benchmark.after_candidate = lambda: request_kernel_campaign_stop(root, run_id)

    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=2)

    stopped = read_kernel_campaign_status(root, run_id)
    champion_before = stopped.champion_attempt_id
    assert generator.calls == [0]
    assert stopped.proposals_evaluated == 1

    resumed_generator = SlateGenerator(["winner", "tiny-gain"])
    resumed_benchmark = StoppingBenchmarkRunner()
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    )
    result = resumed.run(proposals=2)

    assert resumed_generator.calls == [1]
    assert [call[0] for call in resumed_benchmark.calls] == ["tiny-gain"]
    assert result.attempts[1].attempt_id == champion_before
    assert result.champion_attempt_id == champion_before


def test_resume_reconciles_attempt_persisted_before_generation_link(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resume-missing-link"
    generator = SlateGenerator(["winner", "tiny-gain"])
    benchmark = FakeBenchmarkRunner()
    initial = _runner(root, generator, benchmark, run_id=run_id)

    def interrupt_before_link(*_args, **_kwargs):
        raise KeyboardInterrupt

    initial._journal.link_attempt = interrupt_before_link
    with pytest.raises(KeyboardInterrupt):
        initial.run(proposals=2)

    assert generator.calls == [0]
    assert len(tuple((initial.run_dir / "attempts").glob("*.json"))) == 2
    assert not (initial.run_dir / "generation" / "proposals" / "000001" / "attempt-link.json").exists()
    lineage_path = initial.run_dir / "lineage.jsonl"
    baseline_line = lineage_path.read_text(encoding="utf-8").splitlines()[0]
    lineage_path.write_text(f"{baseline_line}\n", encoding="utf-8")

    resumed_generator = SlateGenerator(["winner", "tiny-gain"])
    resumed_benchmark = FakeBenchmarkRunner()
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    )
    result = resumed.run(proposals=2)

    assert resumed_generator.calls == [1]
    assert [call[0] for call in resumed_benchmark.calls] == ["tiny-gain"]
    assert len(result.attempts) == 3
    assert len(lineage_path.read_text(encoding="utf-8").splitlines()) == 3
    assert (resumed.run_dir / "generation" / "proposals" / "000001" / "attempt-link.json").is_file()
    champion_pointer = json.loads((resumed.run_dir / "champion.json").read_text(encoding="utf-8"))
    assert champion_pointer["attempt_id"] == result.attempts[1].attempt_id


def test_resume_refuses_ambiguous_provider_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "ambiguous-provider"
    initial = _runner(
        root,
        SlateGenerator(["winner"], interrupt=True),
        FakeBenchmarkRunner(),
        run_id=run_id,
    )
    with pytest.raises(KeyboardInterrupt):
        initial.run(proposals=1)

    resumed_generator = SlateGenerator(["winner"])
    resumed_benchmark = FakeBenchmarkRunner()
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    )
    with pytest.raises(KernelCampaignAmbiguousExecution, match="provider execution is ambiguous"):
        resumed.run(proposals=1)

    assert resumed_generator.calls == []
    assert resumed_benchmark.calls == []


def test_partial_call_fence_cannot_override_unresolved_call_evidence(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "incomplete-call-fence"
    initial = _runner(
        root,
        IncompleteCallFenceGenerator(["winner"], interrupt=True),
        FakeBenchmarkRunner(),
        run_id=run_id,
    )
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=1)

    manifest = json.loads((initial.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"]["call_fence_resume_safe"] is False
    resumed_generator = IncompleteCallFenceGenerator(["winner"])
    resumed = _runner(
        root,
        resumed_generator,
        FakeBenchmarkRunner(),
        run_id=run_id,
        resume=True,
    )
    with pytest.raises(KernelCampaignAmbiguousExecution, match="provider execution is ambiguous"):
        resumed.run(proposals=1)

    assert resumed_generator.calls == []


def test_explicitly_resumable_external_generation_continues_existing_claim(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resumable-external-claim"
    initial_generator = ResumableClaimGenerator(["winner"], interrupt=True)
    initial = _runner(
        root,
        initial_generator,
        FakeBenchmarkRunner(),
        run_id=run_id,
    )
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=1)

    stopped = read_kernel_campaign_status(root, run_id)
    assert stopped.can_resume
    assert stopped.ambiguity is None

    resumed_generator = ResumableClaimGenerator(["winner"])
    resumed_benchmark = FakeBenchmarkRunner()
    result = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    ).run(proposals=1)

    assert resumed_generator.calls == [0]
    assert [call[0] for call in resumed_benchmark.calls] == ["winner"]
    assert result.champion_source == "winner"


def test_resume_refuses_ambiguous_gpu_execution(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "ambiguous-gpu"
    generator = SlateGenerator(["winner"])
    initial = _runner(
        root,
        generator,
        InterruptingBenchmarkRunner(interrupt=True),
        run_id=run_id,
    )
    with pytest.raises(KeyboardInterrupt):
        initial.run(proposals=1)

    resumed_generator = SlateGenerator(["winner"])
    resumed_benchmark = InterruptingBenchmarkRunner(interrupt=False)
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
    )
    with pytest.raises(KernelCampaignAmbiguousExecution, match="benchmark execution is ambiguous"):
        resumed.run(proposals=1)

    assert resumed_generator.calls == []
    assert resumed_benchmark.calls == []


def test_resume_verifies_content_addressed_generation_source(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "tampered-source"
    generator = SlateGenerator(
        ["winner"],
        after_generate=lambda _generation: request_kernel_campaign_stop(root, run_id),
    )
    initial = _runner(root, generator, FakeBenchmarkRunner(), run_id=run_id)
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=1)

    receipt = KernelCampaignJournal(initial.run_dir, run_id).read_generation_result(1)
    assert receipt is not None
    source_path = initial.run_dir / "artifacts" / (
        f"{receipt.artifact_digest.removeprefix('sha256:')}{receipt.source_suffix}"
    )
    source_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(KernelCampaignJournalError, match="generated source artifact is missing or changed"):
        _runner(
            root,
            SlateGenerator(["winner"]),
            FakeBenchmarkRunner(),
            run_id=run_id,
            resume=True,
        )


def test_resume_verifies_content_addressed_benchmark_report(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "tampered-report"
    benchmark = StoppingBenchmarkRunner()
    initial = _runner(
        root,
        SlateGenerator(["winner", "tiny-gain"]),
        benchmark,
        run_id=run_id,
    )
    benchmark.after_candidate = lambda: request_kernel_campaign_stop(root, run_id)
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=2)

    candidate_attempt = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (initial.run_dir / "attempts").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["generation"] == 1
    )
    report_path = initial.run_dir / "reports" / (
        f"{candidate_attempt['report_digest'].removeprefix('sha256:')}.json"
    )
    report_path.write_text("{}", encoding="utf-8")

    resumed = _runner(
        root,
        SlateGenerator(["winner", "tiny-gain"]),
        StoppingBenchmarkRunner(),
        run_id=run_id,
        resume=True,
    )
    with pytest.raises(ValueError, match="kernel report artifact changed"):
        resumed.run(proposals=2)


def test_complete_campaign_indexes_operator_artifacts(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        SlateGenerator(["winner"]),
        FakeBenchmarkRunner(),
        run_id="artifact-index",
    )
    result = runner.run(proposals=1)

    status = read_kernel_campaign_status(tmp_path, runner.run_id)
    index = json.loads(Path(status.artifact_index_path).read_text(encoding="utf-8"))
    kinds = {artifact["kind"] for artifact in index["artifacts"]}

    assert status.status == "complete"
    assert all(not artifact["path"].endswith(".lock") for artifact in index["artifacts"])
    assert not status.can_resume
    assert status.generation_budget_id == KernelGenerationBudget(proposal_cap=2).budget_id
    assert {
        "manifest",
        "prompt",
        "generation_claim",
        "generation_receipt",
        "evaluation_claim",
        "attempt_link",
        "source",
        "report",
        "attempt",
        "lineage",
        "champion",
        "summary",
    } <= kinds

    replay_generator = SlateGenerator(["winner"])
    replay_benchmark = FakeBenchmarkRunner()
    replay = _runner(
        tmp_path,
        replay_generator,
        replay_benchmark,
        run_id=runner.run_id,
        resume=True,
    ).run(proposals=1)
    assert replay == result
    assert replay_generator.calls == []
    assert replay_benchmark.calls == []

    changed_budget = KernelGenerationBudget(proposal_cap=2, max_cost_usd=50.0)
    changed = _runner(
        tmp_path,
        SlateGenerator(["winner"]),
        FakeBenchmarkRunner(),
        run_id=runner.run_id,
        resume=True,
        budget=changed_budget,
    )
    with pytest.raises(ValueError, match="generation budget changed"):
        changed.run(proposals=1)


def test_resume_recovers_receipt_written_before_result_pointer(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "orphan-receipt"
    initial = _runner(
        root,
        SlateGenerator(
            ["winner"],
            after_generate=lambda _generation: request_kernel_campaign_stop(root, run_id),
        ),
        FakeBenchmarkRunner(),
        run_id=run_id,
    )
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=1)
    pointer = initial.run_dir / "generation" / "proposals" / "000001" / "result.json"
    pointer.unlink()

    generator = SlateGenerator(["must-not-be-dispatched"])
    benchmark = FakeBenchmarkRunner()
    result = _runner(root, generator, benchmark, run_id=run_id, resume=True).run(proposals=1)

    assert generator.calls == []
    assert [call[0] for call in benchmark.calls] == ["winner"]
    assert result.attempts[1].artifact_digest == result.champion_artifact_digest
    assert pointer.is_file()


def test_stop_between_source_return_and_evaluation_claim_prevents_gpu_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "stop-at-evaluation-boundary"
    benchmark = FakeBenchmarkRunner()
    runner = _runner(root, SlateGenerator(["winner"]), benchmark, run_id=run_id)
    original_generate = runner._generate_source

    def generate_then_stop(prompt: str, generation: int) -> str:
        source = original_generate(prompt, generation)
        request_kernel_campaign_stop(root, run_id)
        return source

    runner._generate_source = generate_then_stop
    with pytest.raises(KernelGenerationCancelled):
        runner.run(proposals=1)

    assert [call[0] for call in benchmark.calls] == ["baseline"]
    assert not tuple((runner.run_dir / "generation" / "evaluations").glob("000001.json"))


def test_stop_waits_for_an_admitted_gpu_dispatch_to_finish(tmp_path: Path) -> None:
    run_id = "linearized-gpu-stop"
    journal = KernelCampaignJournal(tmp_path / run_id, run_id)
    journal.run_dir.mkdir(parents=True)
    entered = Event()
    release = Event()
    stop_started = Event()

    def dispatch() -> None:
        with journal.begin_evaluation(
            generation=0,
            role="baseline",
            artifact_digest=content_digest("baseline"),
        ):
            entered.set()
            assert release.wait(timeout=5)

    def stop() -> None:
        stop_started.set()
        journal.request_stop()

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch_future = pool.submit(dispatch)
        assert entered.wait(timeout=5)
        stop_future = pool.submit(stop)
        assert stop_started.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            stop_future.result(timeout=0.05)
        release.set()
        dispatch_future.result(timeout=5)
        stop_future.result(timeout=5)

    assert journal.stop_requested()


def test_resume_binds_full_evolution_prompt_contract(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "prompt-contract"
    initial = _runner(
        root,
        SlateGenerator(
            ["winner"],
            after_generate=lambda _generation: request_kernel_campaign_stop(root, run_id),
        ),
        FakeBenchmarkRunner(),
        run_id=run_id,
    )
    with pytest.raises(KernelGenerationCancelled):
        initial.run(proposals=1)

    changed = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="kernelbench-level1-problem1",
            task_prompt="A materially different evolution objective.",
            baseline_source="baseline",
            min_relative_improvement=0.05,
            target_reference_speedup=3.0,
        ),
        SlateGenerator(["winner"]),
        _evaluator(FakeBenchmarkRunner()),
        root,
        run_id=run_id,
        generation_budget=KernelGenerationBudget(proposal_cap=2),
        resume=True,
    )
    with pytest.raises(ValueError, match="evolution conflicts"):
        changed.run(proposals=1)


def test_complete_resume_validates_named_champion_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "tampered-named-champion"
    runner = _runner(root, SlateGenerator(["winner"]), FakeBenchmarkRunner(), run_id=run_id)
    runner.run(proposals=1)
    (runner.run_dir / "champion.py").write_text("tampered", encoding="utf-8")

    replay = _runner(
        root,
        SlateGenerator(["winner"]),
        FakeBenchmarkRunner(),
        run_id=run_id,
        resume=True,
    )
    with pytest.raises(ValueError, match="named champion source artifact"):
        replay.run(proposals=1)


def test_execution_lease_allows_only_one_fresh_runner_to_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    first_generator = SlateGenerator(["winner"])
    second_generator = SlateGenerator(["winner"])
    first_benchmark = FakeBenchmarkRunner()
    second_benchmark = FakeBenchmarkRunner()
    first = _runner(root, first_generator, first_benchmark, run_id="exclusive-run")
    second = _runner(root, second_generator, second_benchmark, run_id="exclusive-run")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            pool.submit(candidate.run, 1)
            for candidate in (first, second)
        ]
        successes = 0
        failures = 0
        for outcome in outcomes:
            try:
                outcome.result()
                successes += 1
            except FileExistsError:
                failures += 1

    assert (successes, failures) == (1, 1)
    assert len(first_generator.calls) + len(second_generator.calls) == 1
    assert len(first_benchmark.calls) + len(second_benchmark.calls) == 2


def test_resume_restores_durable_failed_call_before_retry(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "resume-provider-backoff"
    budget = KernelGenerationBudget(
        proposal_cap=1,
        max_retries_per_proposal=1,
        retry_backoff_seconds=0,
    )
    provider = MockProvider([ProviderError("503 overloaded", usage={"input_tokens": 2})])
    generator = ProviderKernelGenerator(
        provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    initial = _runner(root, generator, FakeBenchmarkRunner(), run_id=run_id, budget=budget)
    persist_failure = initial._journal.write_generation_failure

    def persist_then_crash(failure: KernelGenerationFailure) -> None:
        persist_failure(failure)
        raise KeyboardInterrupt

    generator.set_failure_observer(persist_then_crash)
    with pytest.raises(KeyboardInterrupt):
        initial.run(proposals=1)

    resumed_provider = MockProvider(
        [
            CompletionResult(
                text=VALID_SOURCE,
                model="mock",
                usage={"input_tokens": 3, "output_tokens": 4},
                cost_usd=0.01,
            )
        ]
    )
    resumed_generator = ProviderKernelGenerator(
        resumed_provider,
        provider_id="mock",
        model="mock",
        budget=budget,
        entrypoint="ModelNew",
    )
    result = _runner(
        root,
        resumed_generator,
        FakeBenchmarkRunner(),
        run_id=run_id,
        resume=True,
        budget=budget,
    ).run(proposals=1)

    receipt = KernelCampaignJournal(root / run_id, run_id).read_generation_result(1)
    assert receipt is not None and receipt.retry_count == 1
    assert len(resumed_provider.calls) == 1
    assert len(result.attempts) == 2
    assert receipt.source == VALID_SOURCE


def test_resume_budget_gates_paid_receipt_after_pointer_write_crash(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "paid-orphan-budget"
    budget = KernelGenerationBudget(proposal_cap=1, max_cost_usd=0.1)
    generator = PaidReceiptGenerator()
    benchmark = FakeBenchmarkRunner()
    initial = _runner(root, generator, benchmark, run_id=run_id, budget=budget)
    persist_result = initial._journal.write_generation_result

    def persist_then_crash(result: KernelGenerationResult) -> None:
        persist_result(result)
        raise KeyboardInterrupt

    initial._journal.write_generation_result = persist_then_crash
    with pytest.raises(KeyboardInterrupt):
        initial.run(proposals=1)

    assert generator.calls == [0]
    assert [call[0] for call in benchmark.calls] == ["baseline"]
    resumed_generator = PaidReceiptGenerator()
    resumed_benchmark = FakeBenchmarkRunner()
    resumed = _runner(
        root,
        resumed_generator,
        resumed_benchmark,
        run_id=run_id,
        resume=True,
        budget=budget,
    )
    with pytest.raises(KernelGenerationBudgetExceeded, match="cost_usd"):
        resumed.run(proposals=1)

    assert resumed_generator.calls == []
    assert resumed_benchmark.calls == []


@pytest.mark.parametrize(
    ("entry", "mutation", "message"),
    [
        ("claim", {"run_id": "other-run"}, "generation claim identity is invalid"),
        (
            "pointer",
            {"receipt_path": "../generation/receipts/forged.json"},
            "content-addressed path",
        ),
        ("evaluation", {"run_id": "other-run"}, "evaluation claim identity is invalid"),
    ],
)
def test_resume_rejects_journal_run_and_path_identity_changes(
    tmp_path: Path,
    entry: str,
    mutation: dict[str, str],
    message: str,
) -> None:
    root = tmp_path / "runs"
    run_id = f"tampered-{entry}"
    initial = _runner(root, SlateGenerator(["winner"]), FakeBenchmarkRunner(), run_id=run_id)
    initial.run(proposals=1)
    paths = {
        "claim": initial.run_dir / "generation" / "proposals" / "000001" / "claim.json",
        "pointer": initial.run_dir / "generation" / "proposals" / "000001" / "result.json",
        "evaluation": initial.run_dir / "generation" / "evaluations" / "000001.json",
    }
    path = paths[entry]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(mutation)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(KernelCampaignJournalError, match=message):
        _runner(
            root,
            SlateGenerator(["winner"]),
            FakeBenchmarkRunner(),
            run_id=run_id,
            resume=True,
        ).run(proposals=1)
