"""production candidate-generation seam for the ambient evaluate stage (AC-891, CI-safe, no mlx).

The evaluate stage takes an injectable ``generate_fn`` that scores a candidate's real generation.
These tests pin the production factory that builds that closure: it serves a candidate's model
once per record (a per-record client cache, so a model is loaded once and reused across every eval
case) and raises a clear error when the record is not servable. Exercised entirely with a fake
client and fake resolver, so no mlx / torch is imported in CI.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autocontext.agents.scenario_bound_clients import LocalClientPlan
from autocontext.ambient.charter import CharterAnchor
from autocontext.config.settings import AppSettings
from autocontext.training.model_registry import DistilledModelRecord


def _record(artifact_id: str) -> DistilledModelRecord:
    return DistilledModelRecord(
        artifact_id=artifact_id,
        scenario="grid_ctf",
        scenario_family="grid",
        backend="mlx",
        checkpoint_path="/ckpt/" + artifact_id,
        runtime_types=["provider"],
        activation_state="candidate",
        training_metrics={},
        provenance={},
    )


class _FakeClient:
    """Stand-in for a served LanguageModelClient; echoes the prompt back."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> SimpleNamespace:
        self.calls.append({"model": model, "prompt": prompt, "max_tokens": max_tokens, "role": role})
        return SimpleNamespace(text="GEN::" + prompt)


def test_generation_serves_generates_and_caches_client_per_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """The closure serves the candidate's model and returns its generation; the client is built ONCE
    per record (reused across that record's eval cases) and rebuilt for a different record."""
    import autocontext.ambient.generation as gen

    built_plans: list[LocalClientPlan] = []

    def fake_plan(record: DistilledModelRecord) -> LocalClientPlan:
        return LocalClientPlan(kind="mlx", model="served-" + record.artifact_id, adapter_path=None, score_conditioned=False)

    def fake_build(plan: LocalClientPlan, settings: AppSettings) -> _FakeClient:
        built_plans.append(plan)
        return _FakeClient()

    monkeypatch.setattr(gen, "plan_local_client", fake_plan)
    monkeypatch.setattr(gen, "build_planned_client", fake_build)

    settings = AppSettings(mlx_temperature=0.7, mlx_max_tokens=256)
    fn = gen.build_candidate_generation_fn(settings)
    anchor = CharterAnchor()
    rec_a = _record("a")

    # two cases for the same record -> one client build (per-record cache)
    assert fn(rec_a, anchor, "hi") == "GEN::hi"
    assert fn(rec_a, anchor, "bye") == "GEN::bye"
    assert len(built_plans) == 1
    # the served model id (from the plan) is what is passed to generate, with the mlx sampling knobs
    assert built_plans[0].model == "served-a"

    # a different record -> a second client build
    rec_b = _record("b")
    assert fn(rec_b, anchor, "x") == "GEN::x"
    assert len(built_plans) == 2


def test_generation_passes_served_model_and_sampling_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate is called with the plan's served model, settings' mlx max_tokens, and the eval role."""
    import autocontext.ambient.generation as gen

    captured: dict[str, object] = {}

    class _CapturingClient:
        def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> SimpleNamespace:
            captured.update(model=model, prompt=prompt, max_tokens=max_tokens, temperature=temperature, role=role)
            return SimpleNamespace(text=prompt.upper())

    monkeypatch.setattr(
        gen,
        "plan_local_client",
        lambda record: LocalClientPlan(kind="mlx", model="the-model", adapter_path=None, score_conditioned=False),
    )
    monkeypatch.setattr(gen, "build_planned_client", lambda plan, settings: _CapturingClient())

    settings = AppSettings(mlx_temperature=0.3, mlx_max_tokens=128)
    fn = gen.build_candidate_generation_fn(settings)
    assert fn(_record("a"), CharterAnchor(), "hello") == "HELLO"
    assert captured == {
        "model": "the-model",
        "prompt": "hello",
        "max_tokens": 128,
        "temperature": 0.3,
        "role": "ambient-evaluate",
    }


def test_generation_raises_when_record_not_servable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A record with no local client plan is not servable, so the closure raises a clear error."""
    import autocontext.ambient.generation as gen

    monkeypatch.setattr(gen, "plan_local_client", lambda record: None)
    monkeypatch.setattr(gen, "build_planned_client", lambda plan, settings: pytest.fail("should not build"))

    fn = gen.build_candidate_generation_fn(AppSettings())
    with pytest.raises(RuntimeError, match="not servable"):
        fn(_record("a"), CharterAnchor(), "hi")
