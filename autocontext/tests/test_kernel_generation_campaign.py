from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from test_kernel_evolution import FakeBenchmarkRunner, _evaluator

from autocontext.kernel_evolution import (
    KernelCampaignAmbiguousExecution,
    KernelCampaignJournal,
    KernelCampaignJournalError,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelGenerationBudget,
    KernelGenerationBudgetExceeded,
    KernelGenerationCancelled,
    KernelGenerationProviderError,
    ProviderKernelGenerator,
    content_digest,
    read_kernel_campaign_status,
    request_kernel_campaign_stop,
)
from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

VALID_SOURCE = "class ModelNew:\n    pass\n"
VALID_SOURCE_TWO = "class ModelNew:\n    version = 2\n"


class MockProvider(LLMProvider):
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
        [CompletionResult(text=VALID_SOURCE, model="mock", cost_usd=0.25)]
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
        [CompletionResult(text=VALID_SOURCE, model="mock", cost_usd=0.01)],
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
        [CompletionResult(text=VALID_SOURCE, model="mock", cost_usd=0.05)],
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
        [CompletionResult(text=VALID_SOURCE_TWO, model="mock", cost_usd=0.07)]
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
