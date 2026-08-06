"""Bounded LLM review gate for ambient advise proposals (AC-900)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.ambient.advise_gate import AdviseGateDecision, GateOutcome, run_advise_gate
from autocontext.ambient.charter import (
    AdviseGateConfig,
    Charter,
    CharterBudgets,
    CharterSource,
    CharterTarget,
)
from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError


class _StubProvider(LLMProvider):
    def __init__(self, text: str = "", error: bool = False) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.calls.append({"model": model, "max_tokens": max_tokens, "user_prompt": user_prompt})
        if self.error:
            raise ProviderError("gate provider down")
        return CompletionResult(text=self.text)

    def default_model(self) -> str:
        return "stub"


def _gate_charter(**overrides: object) -> Charter:
    base: dict[str, object] = dict(
        tier="oss",
        control_surface="local",
        autonomy="propose",
        sources=[CharterSource(name="native", kind="autocontext", enabled=True)],
        targets=[
            CharterTarget(
                name="othello-target",
                kind="task_family",
                selector="othello",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                method="sft-distill",
                min_dataset_records=500,
                eval_suite="grid_ctf_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=200.0),
    )
    base.update(overrides)
    return Charter(**base)  # type: ignore[arg-type]


class TestAdviseGateConfig:
    def test_validates_and_round_trips_on_charter(self) -> None:
        config = AdviseGateConfig(model="judge-model")
        assert config.max_output_tokens == 512
        charter = _gate_charter(advise_gate=config)
        reloaded = Charter.model_validate(charter.model_dump(mode="json"))
        assert reloaded.advise_gate is not None
        assert reloaded.advise_gate.model == "judge-model"
        assert _gate_charter().advise_gate is None

    def test_rejects_empty_model_and_bad_bounds(self) -> None:
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="")
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="m", max_output_tokens=10)
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="m", max_output_tokens=100_000)


class TestRunAdviseGate:
    def test_parses_clean_verdict(self) -> None:
        provider = _StubProvider('{"should_propose": true, "rationale": "durable evidence"}')
        outcome = run_advise_gate(provider, "judge-model", "evidence", max_output_tokens=256)
        assert outcome == GateOutcome(AdviseGateDecision(should_propose=True, rationale="durable evidence"), "")
        assert provider.calls[0]["max_tokens"] == 256
        assert provider.calls[0]["model"] == "judge-model"

    def test_parses_fenced_verdict(self) -> None:
        provider = _StubProvider('```json\n{"should_propose": false, "rationale": "one-off noise"}\n```')
        outcome = run_advise_gate(provider, "m", "evidence", max_output_tokens=512)
        assert outcome.decision is not None and outcome.decision.should_propose is False

    def test_garbage_returns_parse_failure(self) -> None:
        provider = _StubProvider("mid-sentence trunca")
        outcome = run_advise_gate(provider, "m", "evidence", max_output_tokens=512)
        assert outcome == GateOutcome(None, "parse_error")

    def test_provider_error_returns_failure(self) -> None:
        provider = _StubProvider(error=True)
        outcome = run_advise_gate(provider, "m", "evidence", max_output_tokens=512)
        assert outcome == GateOutcome(None, "provider_error")

    def test_unexpected_exception_degrades_not_raises(self) -> None:
        class _TypeErrorProvider(_StubProvider):
            def complete(self, *args, **kwargs):  # type: ignore[override]
                raise TypeError("Could not resolve authentication method")

        outcome = run_advise_gate(_TypeErrorProvider(), "m", "evidence", max_output_tokens=512)
        assert outcome == GateOutcome(None, "provider_error")


class TestAdviseStageGating:
    """The gate filters proposal emission; failure degrades to permit."""

    def _run_stage(self, tmp_path, charter, provider=None):
        from autocontext.ambient.advise import AdviseStage
        from autocontext.ambient.proposals import ProposalStore
        from autocontext.ambient.queue import AmbientQueue
        from autocontext.ambient.stage import StageContext
        from autocontext.ambient.trace_store import TraceStore
        from autocontext.harness.core.events import EventStreamEmitter

        tmp_path.mkdir(parents=True, exist_ok=True)
        traces = TraceStore(tmp_path / "traces.sqlite3")
        for index in range(60):
            traces.append(
                "autocontext-outputs:native",
                "agent_output",
                {
                    "run_id": "run_novel",
                    "scenario": "novel_task",
                    "generation_index": index,
                    "role": "competitor",
                    "content": f"strategy {index}",
                    "status": "completed",
                    "best_score": 0.9,
                },
                "frontier",
                0,
            )
        events: list[tuple[str, dict]] = []
        emitter = EventStreamEmitter(tmp_path / "events.ndjson")
        original_emit = emitter.emit

        def capture(name, payload, **kwargs):
            events.append((name, payload))
            return original_emit(name, payload, **kwargs)

        emitter.emit = capture  # type: ignore[method-assign]
        store = ProposalStore(tmp_path / "proposals.jsonl")
        stage = AdviseStage(
            name="advise",
            trace_store=traces,
            min_traces=50,
            gate_provider=provider,
        )
        ctx = StageContext(
            charter=charter,
            queue=AmbientQueue(tmp_path / "queue.sqlite3"),
            emitter=emitter,
            proposal_store=store,
        )
        stage.run_once(ctx)
        return store.pending(), events, provider

    def test_gate_reject_emits_no_proposals(self, tmp_path) -> None:
        provider = _StubProvider('{"should_propose": false, "rationale": "transient noise"}')
        charter = _gate_charter(advise_gate=AdviseGateConfig(model="judge"))
        pending, events, _ = self._run_stage(tmp_path, charter, provider)
        assert pending == []
        assert any(name == "advise_gate_rejected" and "transient noise" in str(payload) for name, payload in events)
        assert len(provider.calls) == 1

    def test_gate_approve_matches_ungated_run(self, tmp_path) -> None:
        provider = _StubProvider('{"should_propose": true, "rationale": "durable"}')
        gated, _, _ = self._run_stage(tmp_path / "a", _gate_charter(advise_gate=AdviseGateConfig(model="judge")), provider)
        ungated, _, _ = self._run_stage(tmp_path / "b", _gate_charter())
        assert [p.kind for p in gated] == [p.kind for p in ungated] != []

    def test_gate_failure_degrades_to_permit(self, tmp_path) -> None:
        provider = _StubProvider(error=True)
        charter = _gate_charter(advise_gate=AdviseGateConfig(model="judge"))
        pending, events, _ = self._run_stage(tmp_path, charter, provider)
        assert pending != []
        assert any(name == "advise_gate_degraded" for name, _ in events)

    def test_no_gate_config_never_calls_provider(self, tmp_path) -> None:
        provider = _StubProvider('{"should_propose": false, "rationale": "x"}')
        pending, _, _ = self._run_stage(tmp_path, _gate_charter(), provider)
        assert pending != []
        assert provider.calls == []


class TestFactoryWiring:
    """The gate provider is built only when the charter enables the gate,
    and a misconfigured provider degrades to gate-off instead of crashing."""

    def _build(self, tmp_path, charter, monkeypatch, provider_factory):
        import autocontext.providers as providers_module
        from autocontext.ambient.stage_factory import build_stages
        from autocontext.config.settings import AppSettings
        from autocontext.harness.core.events import EventStreamEmitter

        monkeypatch.setattr(providers_module, "get_provider", provider_factory)
        return build_stages(
            charter,
            db_path=tmp_path / "ambient.sqlite3",
            emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
            runs_db_path=tmp_path / "runs.sqlite3",
            otel_feed_dir=tmp_path / "feed",
            datasets_dir=tmp_path / "datasets",
            registry_dir=tmp_path / "registry",
            usage_db=tmp_path / "usage.sqlite3",
            artifacts_dir=tmp_path / "artifacts",
            checkpoints_dir=tmp_path / "checkpoints",
            suites_dir=tmp_path / "suites",
            settings=AppSettings(),
        )

    def test_gate_charter_builds_provider(self, tmp_path, monkeypatch) -> None:
        sentinel = _StubProvider("{}")
        stages = self._build(
            tmp_path,
            _gate_charter(advise_gate=AdviseGateConfig(model="judge")),
            monkeypatch,
            lambda settings: sentinel,
        )
        assert stages["advise"].gate_provider is sentinel  # type: ignore[union-attr]

    def test_default_charter_never_builds_provider(self, tmp_path, monkeypatch) -> None:
        def boom(settings):
            raise AssertionError("provider must not be built without a gate config")

        stages = self._build(tmp_path, _gate_charter(), monkeypatch, boom)
        assert stages["advise"].gate_provider is None  # type: ignore[union-attr]

    def test_provider_failure_degrades_to_gate_off(self, tmp_path, monkeypatch) -> None:
        def broken(settings):
            raise TypeError("no api key")

        stages = self._build(
            tmp_path,
            _gate_charter(advise_gate=AdviseGateConfig(model="judge")),
            monkeypatch,
            broken,
        )
        assert stages["advise"].gate_provider is None  # type: ignore[union-attr]
