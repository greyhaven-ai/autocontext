"""AC-934: a dollar budget that can never fire says so.

Two gates are denominated in USD -- `consultation_cost_budget`, and
`cost_budget_limit` / `cost_throttle_above_total` in the generation pipeline.
Spend accumulates from `CompletionResult.cost_usd`, which is None for local
providers, so on an all-local run the total stays at zero and neither gate ever
fires.

**Not fixed by making a dollar budget bound something else.** The setting says
dollars, a local run spends no dollars, and not firing is correct. Quietly
repurposing it as a wall-clock or token bound would hand the operator a limit
they never asked for in a unit the name does not say -- the silent substitution
this codebase keeps removing.

What was missing is that nobody was told. These pin that the advisory fires
exactly when the gate is genuinely unreachable, and stays quiet otherwise: a
warning on a working cloud run is one operators learn to ignore, which would put
us back where we started.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config.settings import load_settings
from autocontext.preflight import PreflightChecker


def _advisories(monkeypatch: pytest.MonkeyPatch, **env: str) -> list:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    checker = PreflightChecker("grid_ctf", knowledge_root=Path("/tmp"), settings=settings)
    return checker.check_budget_gates_can_fire()


def test_local_run_with_a_dollar_budget_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="ollama",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert len(results) == 1
    assert results[0].name == "budget_gate_inert"
    assert "AUTOCONTEXT_COST_BUDGET_LIMIT" in results[0].detail


def test_the_warning_is_advisory_not_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """A working local run must not be refused over an inert gate.

    The configuration is not wrong -- it is merely unreachable -- and blocking
    would be worse than the gap being reported.
    """
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="ollama",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert results[0].blocking is False
    assert PreflightChecker.blocking_failures(results) == []


def test_no_warning_without_a_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _advisories(
            monkeypatch,
            AUTOCONTEXT_AGENT_PROVIDER="ollama",
            AUTOCONTEXT_ROLE_ROUTING="auto",
        )
        == []
    )


def test_no_warning_on_a_priced_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate is reachable here, so there is nothing to report.

    This is the case that keeps the warning worth reading.
    """
    assert (
        _advisories(
            monkeypatch,
            AUTOCONTEXT_AGENT_PROVIDER="anthropic",
            AUTOCONTEXT_ROLE_ROUTING="auto",
            AUTOCONTEXT_COST_BUDGET_LIMIT="5",
        )
        == []
    )


@pytest.mark.parametrize(
    "env_var",
    ["AUTOCONTEXT_COST_BUDGET_LIMIT", "AUTOCONTEXT_COST_THROTTLE_ABOVE_TOTAL", "AUTOCONTEXT_CONSULTATION_COST_BUDGET"],
)
def test_every_dollar_denominated_setting_is_covered(monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
    """All three, not just the one that prompted the issue.

    Covering one and missing two would leave the same silent gap behind a
    warning that looks like coverage.
    """
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="ollama",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        **{env_var: "5"},
    )
    assert len(results) == 1
    assert env_var in results[0].detail


def test_the_message_points_at_a_bound_that_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming the problem without an alternative leaves the operator stuck."""
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="ollama",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
    )
    assert "AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS" in results[0].detail


def test_the_message_changes_when_a_time_budget_is_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telling someone to set what they already set reads as a broken check."""
    results = _advisories(
        monkeypatch,
        AUTOCONTEXT_AGENT_PROVIDER="ollama",
        AUTOCONTEXT_ROLE_ROUTING="auto",
        AUTOCONTEXT_COST_BUDGET_LIMIT="5",
        AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS="600",
    )
    assert "already set (600s)" in results[0].detail


def test_the_dollar_gates_themselves_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix is visibility, not a change in what the gates measure.

    If a later edit makes a dollar budget bound something other than dollars,
    this is the test that should stop it.
    """
    monkeypatch.setenv("AUTOCONTEXT_COST_BUDGET_LIMIT", "5")
    settings = load_settings()
    assert settings.cost_budget_limit == 5.0
