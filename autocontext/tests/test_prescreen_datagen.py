# autocontext/tests/test_prescreen_datagen.py
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autocontext.prescreen.datagen import (
    CallBudgetExhausted,
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
    def __init__(self, cost: float | None = None) -> None:
        self.calls = 0
        self.cost = cost

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
        return CompletionResult(text=f"draft {self.calls}", cost_usd=self.cost)

    def default_model(self) -> str:
        return "fake"


class ScriptedTask(AgentTaskInterface):
    """Fails rounds 1 and 2, passes round 3; each revision spends one provider call."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.evaluations = 0

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
        return AgentTaskResult(score=score, reasoning="ok", dimension_scores={"q": score}, evaluator_epoch="epoch-fake")

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return self.provider.complete("revise", output).text


def factory(task, provider, protocol):  # type: ignore[no-untyped-def]
    return ScriptedTask(provider)


def write_protocol(tmp_path: Path, **overrides: object) -> Path:
    spec: dict[str, object] = {
        "protocol_version": "test-v1",
        "model": "fake",
        "repetitions": 5,
        "max_rounds": 5,
        "quality_threshold": 0.9,
        "judge_samples": 1,
        "max_provider_calls": 1000,
        "max_reported_cost_usd": 100.0,
        "target_eligible_rounds": 4,
        "corpus": "tasks.jsonl",
    }
    spec.update(overrides)
    (tmp_path / "tasks.jsonl").write_text(
        "\n".join(json.dumps({"task_id": t, "category": "writing", "prompt": f"p {t}", "rubric": "r"}) for t in ("t1", "t2"))
    )
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(spec))
    return path


def store_for(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(MIGRATIONS)
    return store


def test_committed_protocol_loads_with_24_unique_tasks() -> None:
    spec, raw = load_protocol(BENCH / "protocol.json")
    assert isinstance(spec, DatagenProtocol) and spec.protocol_version == "judge-prescreen-datagen-v1"
    assert len(spec.corpus) == 24 and len({t.task_id for t in spec.corpus}) == 24
    assert {t.category for t in spec.corpus} == {"writing", "code", "analysis", "planning"}
    assert raw == (BENCH / "protocol.json").read_bytes()


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
    path = write_protocol(tmp_path)
    (tmp_path / "tasks.jsonl").write_text(
        json.dumps({"task_id": "x", "category": "c", "prompt": "p", "rubric": "r"})
        + "\n"
        + json.dumps({"task_id": "x", "category": "c", "prompt": "p", "rubric": "r"})
    )
    with pytest.raises(ValueError, match="duplicate task_id"):
        load_protocol(path)


def test_counting_provider_enforces_both_caps_and_keeps_the_provider_identity() -> None:
    inner = FakeProvider(cost=1.0)
    capped = CountingProvider(inner, max_calls=3, max_cost_usd=2.0)
    assert capped.name == inner.name and capped.default_model() == "fake"
    capped.complete("s", "u")
    capped.complete("s", "u")
    with pytest.raises(CallBudgetExhausted, match="cost"):
        capped.complete("s", "u")
    calls_only = CountingProvider(FakeProvider(), max_calls=1, max_cost_usd=100.0)
    calls_only.complete("s", "u")
    with pytest.raises(CallBudgetExhausted, match="calls"):
        calls_only.complete("s", "u")


def test_run_stops_at_the_eligible_target_and_records_identity(tmp_path: Path) -> None:
    spec, raw = load_protocol(write_protocol(tmp_path))
    store = store_for(tmp_path)
    out = tmp_path / "run"
    summary = run_datagen(spec, raw, out_dir=out, store=store, provider=FakeProvider(), task_factory=factory, log=lambda _: None)
    assert summary.stop_reason == "target_reached" and summary.loops == 2 and summary.eligible_rounds == 4
    identity = json.loads((out / "identity.json").read_text())
    assert identity["protocol_sha256"] == hashlib.sha256(raw).hexdigest()
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
