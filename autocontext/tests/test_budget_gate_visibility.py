"""AC-934: a dollar budget that cannot accumulate spend says so.

Generation and consultation do not share a cost accumulator. Generation prices
``RoleUsage`` by model, while consultation persists provider-reported
``CompletionResult.cost_usd``. These tests pin each path independently so a
route's hosting label is never mistaken for the value a live gate consumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config.settings import load_settings
from autocontext.harness.core.types import RoleUsage
from autocontext.harness.meta_optimizer import MetaOptimizer
from autocontext.preflight import PreflightChecker


def _advisories(monkeypatch: pytest.MonkeyPatch, *, knowledge_root: Path | None = None, **env: str) -> list:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    checker = PreflightChecker(
        "grid_ctf",
        knowledge_root=knowledge_root or Path("/tmp"),
        settings=settings,
    )
    return checker.check_budget_gates_can_fire()


def test_mlx_run_with_a_generation_dollar_budget_is_warned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    results = _advisories(
        monkeypatch,
        knowledge_root=tmp_path,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_MLX_MODEL_PATH=str(model_path),
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert len(results) == 1
    assert results[0].name == "generation_budget_gate_inert"
    assert "AUTOCONTEXT_COST_BUDGET_LIMIT" in results[0].detail
    assert "MLX" in results[0].detail


def test_discovered_local_artifact_uses_the_same_route_as_orchestration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    results = _advisories(
        monkeypatch,
        knowledge_root=tmp_path,
        AUTOCONTEXT_AGENT_PROVIDER="anthropic",
        AUTOCONTEXT_MLX_MODEL_PATH=str(model_path),
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert len(results) == 1
    assert "every generation role uses MLX" in results[0].detail


def test_the_warning_is_advisory_not_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert results[0].blocking is False
    assert PreflightChecker.blocking_failures(results) == []


def test_no_warning_without_a_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _advisories(monkeypatch, AUTOCONTEXT_AGENT_PROVIDER="mlx", AUTOCONTEXT_ROLE_ROUTING="auto") == []


@pytest.mark.parametrize("provider", ["anthropic", "ollama", "vllm"])
def test_model_priced_generation_routes_are_not_called_inert(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    """These routes can advance CostCalculator even when locally hosted."""
    assert (
        _advisories(
            monkeypatch,
            AUTOCONTEXT_AGENT_PROVIDER=provider,
            AUTOCONTEXT_ROLE_ROUTING="auto",
            AUTOCONTEXT_COST_BUDGET_LIMIT="5",
        )
        == []
    )


def test_ollama_advisory_matches_the_live_generation_accumulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("AUTOCONTEXT_ROLE_ROUTING", "auto")
    monkeypatch.setenv("AUTOCONTEXT_COST_BUDGET_LIMIT", "0.001")
    settings = load_settings()

    assert PreflightChecker("grid_ctf", settings=settings).check_budget_gates_can_fire() == []

    optimizer = MetaOptimizer.from_settings(settings)
    optimizer.record_llm_call(
        "competitor",
        RoleUsage(input_tokens=1_000, output_tokens=0, latency_ms=0, model="llama3.1"),
        generation=1,
    )
    summary = optimizer.cost_summary()
    assert summary is not None
    assert summary.total_cost == 0.003
    assert summary.total_cost >= settings.cost_budget_limit


@pytest.mark.parametrize(
    "env_var",
    ["AUTOCONTEXT_COST_BUDGET_LIMIT", "AUTOCONTEXT_COST_THROTTLE_ABOVE_TOTAL"],
)
def test_each_generation_dollar_setting_is_covered(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        **{env_var: "5"},
    )
    assert len(results) == 1
    assert env_var in results[0].detail


def test_disabled_generation_cost_tracking_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="anthropic",
        AUTOCONTEXT_COST_TRACKING_ENABLED="false",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert len(results) == 1
    assert "cost tracking is disabled" in results[0].detail


def test_tree_exploration_generation_budget_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="anthropic",
        AUTOCONTEXT_EXPLORATION_MODE="tree",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert len(results) == 1
    assert "tree exploration bypasses" in results[0].detail


def test_paid_consultation_budget_is_evaluated_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """A priced generation route must not suppress an inert consultation gate."""
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="anthropic",
        AUTOCONTEXT_CONSULTATION_ENABLED="true",
        AUTOCONTEXT_CONSULTATION_PROVIDER="anthropic",
        AUTOCONTEXT_CONSULTATION_API_KEY="configured",
        AUTOCONTEXT_CONSULTATION_COST_BUDGET="5",
    )
    assert len(results) == 1
    assert results[0].name == "consultation_budget_gate_inert"
    assert "CompletionResult.cost_usd" in results[0].detail


def test_disabled_consultation_budget_explains_why_it_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_CONSULTATION_ENABLED="false",
        AUTOCONTEXT_CONSULTATION_COST_BUDGET="5",
    )
    assert len(results) == 1
    assert "consultation is disabled" in results[0].detail


def test_generation_and_consultation_advisories_can_both_be_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
        AUTOCONTEXT_CONSULTATION_ENABLED="true",
        AUTOCONTEXT_CONSULTATION_COST_BUDGET="5",
    )
    assert {result.name for result in results} == {
        "generation_budget_gate_inert",
        "consultation_budget_gate_inert",
    }


def test_time_budget_hint_does_not_promise_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert "AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS" in results[0].detail
    assert "does not cancel an in-flight provider call" in results[0].detail


def test_time_budget_hint_is_honest_when_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="mlx",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
        AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS="600",
    )
    assert "already set (600s)" in results[0].detail
    assert "after calls return" in results[0].detail
    assert "does not cancel" in results[0].detail


def test_the_dollar_gates_themselves_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_COST_BUDGET_LIMIT", "5")
    settings = load_settings()
    assert settings.cost_budget_limit == 5.0
