# autocontext/tests/test_prescreen_datagen.py
from __future__ import annotations

import hashlib
import itertools
import json
import re
from pathlib import Path

import pytest

from autocontext.execution.judge_spec import JudgeServingSpec
from autocontext.prescreen import datagen
from autocontext.prescreen.datagen import (
    CallBudgetExhausted,
    CostNotReported,
    CountingProvider,
    DatagenProtocol,
    load_protocol,
    run_datagen,
)
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore

BENCH = Path(__file__).resolve().parent.parent / "benchmarks" / "judge_prescreen"
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class FakeProvider(LLMProvider):
    def __init__(
        self, cost: float | None = None, usage: dict[str, int] | None = None, served_model: str | None = None
    ) -> None:
        self.calls = 0
        self.cost = cost
        self.usage = usage or {}
        self.served_model = served_model

    def complete(
        self,
        system_prompt,
        user_prompt,
        model=None,
        temperature=0.0,
        max_tokens=4096,  # type: ignore[no-untyped-def]
        output_schema=None,
    ):
        self.calls += 1
        return CompletionResult(
            text=f"draft {self.calls}", model="fake", served_model=self.served_model, usage=dict(self.usage), cost_usd=self.cost
        )

    def default_model(self) -> str:
        return "fake"


class ScriptedTask(AgentTaskInterface):
    """Fails rounds 1 and 2, passes round 3; each revision spends one provider call.

    The verdicts carry a serving spec from `judge_model`, as LLMJudge's do; None records no spec (no judge identity).
    """

    def __init__(self, provider: LLMProvider, judge_model: str | None = "fake-judge") -> None:
        self.provider = provider
        self.evaluations = 0
        self.spec = (
            JudgeServingSpec(
                compiled_rubric="rubric",
                judge_provider="fake",
                judge_model=judge_model,
                prompt_template_version="test-template",
                score_transformations=("clamp",),
            )
            if judge_model is not None
            else None
        )

    def get_task_prompt(self, state: dict) -> str:
        return "prompt"

    def get_rubric(self) -> str:
        return "rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "prompt"

    def evaluate_output(
        self,
        output,
        state,
        reference_context=None,
        required_concepts=None,  # type: ignore[no-untyped-def]
        calibration_examples=None,
        pinned_dimensions=None,
    ):
        self.evaluations += 1
        score = 0.95 if self.evaluations >= 3 else 0.3 + 0.1 * self.evaluations
        return AgentTaskResult(
            score=score,
            reasoning="ok",
            dimension_scores={"q": score},
            evaluator_epoch=self.spec.epoch_id if self.spec is not None else None,
            evaluator_spec=self.spec.canonical_json() if self.spec is not None else None,
        )

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return self.provider.complete("revise", output).text


def factory(task, provider, protocol):  # type: ignore[no-untyped-def]
    return ScriptedTask(provider)


def write_protocol(tmp_path: Path, *, task_ids: tuple[str, ...] = ("t1", "t2"), **overrides: object) -> Path:
    """A token-cap-only protocol (no dollar cap, as committed) over a corpus it pins by sha256."""
    corpus = "\n".join(
        json.dumps({"task_id": t, "category": "writing", "prompt": f"p {t}", "rubric": "r"}) for t in task_ids
    ).encode("utf-8")
    (tmp_path / "tasks.jsonl").write_bytes(corpus)
    spec: dict[str, object] = {
        "protocol_version": "test-v1",
        "model": "fake",
        "repetitions": 5,
        "max_rounds": 5,
        "quality_threshold": 0.9,
        "judge_samples": 1,
        "max_provider_calls": 1000,
        "max_total_tokens": 1_000_000,
        "max_reported_cost_usd": None,
        "target_eligible_rounds": 4,
        "corpus": "tasks.jsonl",
        "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
    }
    spec.update(overrides)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(spec))
    return path


def store_for(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(MIGRATIONS)
    return store


def test_committed_protocol_loads_with_24_unique_tasks() -> None:
    spec, raw = load_protocol(BENCH / "protocol.json")
    assert isinstance(spec, DatagenProtocol) and spec.protocol_version == "judge-prescreen-datagen-v2"
    assert len(spec.corpus) == 24 and len({t.task_id for t in spec.corpus}) == 24
    assert {t.category for t in spec.corpus} == {"writing", "code", "analysis", "planning"}
    assert raw == (BENCH / "protocol.json").read_bytes()
    assert (spec.max_provider_calls, spec.max_total_tokens, spec.max_reported_cost_usd) == (1500, 3_000_000, None)
    assert spec.corpus_sha256 == hashlib.sha256((BENCH / "tasks.jsonl").read_bytes()).hexdigest()


def test_load_protocol_rejects_bad_input(tmp_path: Path) -> None:
    path = write_protocol(tmp_path)
    spec = json.loads(path.read_text())
    del spec["model"]
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="missing protocol keys"):
        load_protocol(path)
    path = write_protocol(tmp_path, repetitions=0)
    with pytest.raises(ValueError, match="repetitions"):
        load_protocol(path)
    for key in ("max_total_tokens", "corpus_sha256", "max_reported_cost_usd"):
        path = write_protocol(tmp_path)
        spec = json.loads(path.read_text())
        del spec[key]
        path.write_text(json.dumps(spec))
        with pytest.raises(ValueError, match=f"missing protocol keys: \\['{key}'\\]"):
            load_protocol(path)
    with pytest.raises(ValueError, match="max_total_tokens"):
        load_protocol(write_protocol(tmp_path, max_total_tokens=0))
    with pytest.raises(ValueError, match="max_reported_cost_usd"):
        load_protocol(write_protocol(tmp_path, max_reported_cost_usd=0))
    assert load_protocol(write_protocol(tmp_path, max_reported_cost_usd=25.0))[0].max_reported_cost_usd == 25.0
    with pytest.raises(ValueError, match="duplicate task_id"):
        load_protocol(write_protocol(tmp_path, task_ids=("x", "x")))


def test_load_protocol_rejects_a_corpus_that_does_not_match_its_pinned_sha256(tmp_path: Path) -> None:
    path = write_protocol(tmp_path)
    load_protocol(path)
    corpus = tmp_path / "tasks.jsonl"
    corpus.write_bytes(corpus.read_bytes().replace(b"p t2", b"p t2, edited after registration"))
    with pytest.raises(ValueError, match="corpus_sha256"):
        load_protocol(path)


def test_counting_provider_enforces_the_call_and_dollar_caps_and_keeps_the_provider_identity() -> None:
    inner = FakeProvider(cost=1.0)
    capped = CountingProvider(inner, max_calls=3, max_total_tokens=1_000_000, max_cost_usd=2.0)
    assert capped.name == inner.name and capped.default_model() == "fake"
    capped.complete("s", "u")
    capped.complete("s", "u")
    with pytest.raises(CallBudgetExhausted, match="cost") as refused:
        capped.complete("s", "u")
    assert refused.value.stop_reason == "cost_budget_exhausted"
    calls_only = CountingProvider(FakeProvider(), max_calls=1, max_total_tokens=1_000_000, max_cost_usd=None)
    calls_only.complete("s", "u")
    with pytest.raises(CallBudgetExhausted, match="calls") as refused:
        calls_only.complete("s", "u")
    assert refused.value.stop_reason == "call_budget_exhausted"


def test_counting_provider_adds_up_tokens_and_refuses_calls_at_the_token_cap() -> None:
    capped = CountingProvider(
        FakeProvider(usage={"input_tokens": 60, "output_tokens": 40}), max_calls=100, max_total_tokens=250, max_cost_usd=None
    )
    for _ in range(3):  # 100, 200, then 300 tokens: a call is refused only once the total is at or above the cap
        capped.complete("s", "u")
    assert (capped.calls, capped.total_tokens) == (3, 300)
    with pytest.raises(CallBudgetExhausted, match="tokens") as refused:
        capped.complete("s", "u")
    assert refused.value.stop_reason == "token_budget_exhausted" and capped.calls == 3
    openai_names = CountingProvider(
        FakeProvider(usage={"prompt_tokens": 7, "completion_tokens": 5}), max_calls=10, max_total_tokens=100, max_cost_usd=None
    )
    openai_names.complete("s", "u")
    assert openai_names.total_tokens == 12


def test_a_dollar_cap_fails_closed_when_the_provider_reports_no_cost() -> None:
    capped = CountingProvider(FakeProvider(cost=None), max_calls=10, max_total_tokens=1_000_000, max_cost_usd=5.0)
    with pytest.raises(CostNotReported):
        capped.complete("s", "u")
    token_cap_only = CountingProvider(FakeProvider(cost=None), max_calls=10, max_total_tokens=1_000_000, max_cost_usd=None)
    assert token_cap_only.complete("s", "u").text == "draft 1"


def test_run_stops_at_the_eligible_target_and_records_identity(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path))
    store = store_for(tmp_path)
    out = tmp_path / "run"
    summary = run_datagen(spec, raw, out_dir=out, store=store, provider=FakeProvider(), task_factory=factory, log=lambda _: None)
    assert summary.stop_reason == "target_reached" and summary.loops == 2 and summary.eligible_rounds == 4
    assert summary.judge_identities == 1
    identity = json.loads((out / "identity.json").read_text())
    assert identity["protocol_sha256"] == hashlib.sha256(raw).hexdigest()
    assert identity["corpus_sha256"] == hashlib.sha256((tmp_path / "tasks.jsonl").read_bytes()).hexdigest()
    assert identity["git_sha"] is None or re.fullmatch(r"[0-9a-f]{40}", identity["git_sha"])
    assert set(identity) == {"protocol_version", "protocol_sha256", "corpus_sha256", "git_sha", "run_prefix", "started_at"}
    assert (out / "protocol.json").read_bytes() == raw
    rows = store.list_judge_ledger_rows(scenario_family="datagen")
    assert rows and all(r["run_id"].startswith(summary.run_prefix) for r in rows)
    assert {r["scenario_name"] for r in rows} == {"writing/t1", "writing/t2"}
    assert "p t1" not in (out / "summary.json").read_text()


def test_run_stops_when_the_call_cap_is_hit(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path, max_provider_calls=4, target_eligible_rounds=1000))
    summary = run_datagen(
        spec,
        raw,
        out_dir=tmp_path / "run",
        store=store_for(tmp_path),
        provider=FakeProvider(),
        task_factory=factory,
        log=lambda _: None,
    )
    assert summary.stop_reason == "call_budget_exhausted" and summary.provider_calls == 4


def test_run_refuses_an_existing_output_directory(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path))
    (tmp_path / "run").mkdir()
    with pytest.raises(FileExistsError):
        run_datagen(
            spec,
            raw,
            out_dir=tmp_path / "run",
            store=store_for(tmp_path),
            provider=FakeProvider(),
            task_factory=factory,
            log=lambda _: None,
        )


def test_the_target_counts_the_largest_judge_identity_cell(tmp_path: Path) -> None:
    loops = itertools.count()

    def alternating(task, provider, protocol):  # type: ignore[no-untyped-def]
        return ScriptedTask(provider, judge_model=f"judge-{next(loops) % 2}")

    spec, raw = load_protocol(write_protocol(tmp_path))
    summary = run_datagen(
        spec,
        raw,
        out_dir=tmp_path / "run",
        store=store_for(tmp_path),
        provider=FakeProvider(),
        task_factory=alternating,
        log=lambda _: None,
    )
    # Each loop records 2 eligible rounds and the judges alternate, so one cell holds 4 only after the third loop.
    assert summary.stop_reason == "target_reached" and summary.loops == 3
    assert (summary.judged_rounds, summary.eligible_rounds, summary.judge_identities) == (9, 4, 2)
    assert json.loads((tmp_path / "run" / "summary.json").read_text())["judge_identities"] == 2


def test_rounds_without_a_serving_spec_never_count_toward_the_target(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path, repetitions=1))
    summary = run_datagen(
        spec,
        raw,
        out_dir=tmp_path / "run",
        store=store_for(tmp_path),
        provider=FakeProvider(),
        task_factory=lambda task, provider, protocol: ScriptedTask(provider, judge_model=None),
        log=lambda _: None,
    )
    assert summary.stop_reason == "corpus_exhausted" and summary.loops == 2
    assert (summary.judged_rounds, summary.eligible_rounds, summary.judge_identities) == (6, 0, 0)


def test_run_stops_at_the_token_cap_and_reports_tokens_and_the_served_model(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path, max_total_tokens=250, target_eligible_rounds=1000))
    provider = FakeProvider(usage={"input_tokens": 80, "output_tokens": 20}, served_model="fake-2026-09-01")
    out = tmp_path / "run"
    summary = run_datagen(
        spec,
        raw,
        out_dir=out,
        store=store_for(tmp_path),
        provider=provider,
        task_factory=factory,
        log=lambda _: None,
    )
    # Loop 1 spends 3 calls (draft and two revisions, 100 tokens each); loop 2's draft is refused at 300 >= 250.
    assert summary.stop_reason == "token_budget_exhausted" and summary.loops == 1
    assert (summary.provider_calls, summary.total_tokens, summary.served_model) == (3, 300, "fake-2026-09-01")
    assert json.loads((out / "progress.json").read_text())["total_tokens"] == 300
    written = json.loads((out / "summary.json").read_text())
    assert (written["total_tokens"], written["served_model"], written["stop_reason"]) == (
        300,
        "fake-2026-09-01",
        "token_budget_exhausted",
    )


def test_served_model_falls_back_to_the_reported_model(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path, repetitions=1))
    summary = run_datagen(
        spec,
        raw,
        out_dir=tmp_path / "run",
        store=store_for(tmp_path),
        provider=FakeProvider(),
        task_factory=factory,
        log=lambda _: None,
    )
    assert summary.served_model == "fake" and summary.total_tokens == 0


def test_run_stops_as_cost_not_reported_when_a_dollar_cap_meets_a_provider_without_cost(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path, max_reported_cost_usd=5.0))
    out = tmp_path / "run"
    summary = run_datagen(
        spec,
        raw,
        out_dir=out,
        store=store_for(tmp_path),
        provider=FakeProvider(cost=None),
        task_factory=factory,
        log=lambda _: None,
    )
    assert (summary.stop_reason, summary.loops, summary.provider_calls) == ("cost_not_reported", 0, 1)
    assert json.loads((out / "summary.json").read_text())["stop_reason"] == "cost_not_reported"


def test_an_unexpected_error_still_writes_the_summary_and_is_re_raised(tmp_path: Path) -> None:
    built = itertools.count()

    def breaks_on_the_second_loop(task, provider, protocol):  # type: ignore[no-untyped-def]
        if next(built) == 1:
            raise RuntimeError("task factory broke")
        return ScriptedTask(provider)

    spec, raw = load_protocol(write_protocol(tmp_path, target_eligible_rounds=1000))
    out = tmp_path / "run"
    with pytest.raises(RuntimeError, match="task factory broke"):
        run_datagen(
            spec,
            raw,
            out_dir=out,
            store=store_for(tmp_path),
            provider=FakeProvider(),
            task_factory=breaks_on_the_second_loop,
            log=lambda _: None,
        )
    written = json.loads((out / "summary.json").read_text())
    assert (written["stop_reason"], written["loops"], written["judged_rounds"]) == ("error:RuntimeError", 1, 3)


def test_identity_records_no_git_sha_outside_a_git_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(datagen.subprocess, "run", no_git)
    spec, raw = load_protocol(write_protocol(tmp_path, repetitions=1))
    out = tmp_path / "run"
    run_datagen(
        spec,
        raw,
        out_dir=out,
        store=store_for(tmp_path),
        provider=FakeProvider(),
        task_factory=factory,
        log=lambda _: None,
    )
    assert json.loads((out / "identity.json").read_text())["git_sha"] is None
