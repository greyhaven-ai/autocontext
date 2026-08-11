"""AC-914: endpoint checks, and the fact that preflight now runs at all.

`PreflightChecker` existed since it was written and was never called from
anywhere -- `git log -S "PreflightChecker("` across all branches returns
nothing. So the endpoint probes are only half the work; the other half is that
a run is checked before it starts.

Offline by construction: the probes are substituted, because a test that needs
a live server is a test that gets skipped.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import typer


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo every AUTOCONTEXT_* change after each test.

    An earlier version of this file deleted them from os.environ directly and
    leaked across the suite, breaking test_settings_cleanup -- the exact class
    of failure it was asserting against.
    """
    for key in list(os.environ):
        if key.startswith("AUTOCONTEXT_"):
            monkeypatch.delenv(key, raising=False)


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Any:
    """Load real settings with overrides that monkeypatch can undo.

    Setting os.environ directly here leaked across the suite and broke
    test_settings_cleanup -- which exists to catch exactly that.
    """
    from autocontext.config.settings import load_settings

    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def test_non_probeable_transports_are_skipped_not_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that cannot apply must report nothing, not a pass or a failure.

    Anthropic and the CLI runtimes have no OpenAI-compatible surface to probe.
    Reporting a pass would be a lie; reporting a failure would break the
    shipped default path, which is the one configuration that must not change.
    """
    from autocontext.endpoint_probe import resolve_agent_endpoint

    for transport in ("anthropic", "claude-cli", "codex", "pi", "mlx"):
        assert resolve_agent_endpoint(_settings(monkeypatch, AUTOCONTEXT_AGENT_PROVIDER=transport)) is None, transport


def test_probeable_transport_resolves_the_endpoint_a_run_would_use(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.endpoint_probe import resolve_agent_endpoint

    resolved = resolve_agent_endpoint(
        _settings(monkeypatch, AUTOCONTEXT_AGENT_PROVIDER="ollama", AUTOCONTEXT_LOCAL_MODEL="llama3.1:8b")
    )
    assert resolved == ("http://localhost:11434/v1", "no-key", "llama3.1:8b")


def test_default_base_urls_match_the_provider_registry() -> None:
    """The probe must target the same URL create_provider would.

    Duplicating these defaults is how AC-933 happened -- two copies of provider
    resolution, one of them wrong. This asserts the copies agree rather than
    trusting them to.
    """
    import inspect

    from autocontext.endpoint_probe import _DEFAULT_BASE_URL
    from autocontext.providers import registry

    source = inspect.getsource(registry.create_provider)
    for transport in ("ollama", "vllm", "openrouter"):
        assert f'"{_DEFAULT_BASE_URL[transport]}"' in source, (
            f"{transport}: probe default {_DEFAULT_BASE_URL[transport]} not found in create_provider"
        )


def test_an_uncertain_probe_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule that keeps preflight from becoming the thing that breaks runs.

    "Could not determine" is not "broken". Only certain failures stop a run.
    """
    from autocontext.endpoint_probe import ProbeResult
    from autocontext.preflight import PreflightChecker

    monkeypatch.setattr(
        "autocontext.endpoint_probe.probe_endpoint",
        lambda *_: [
            ProbeResult(name="endpoint_reachable", passed=True, certain=True, detail="ok"),
            ProbeResult(name="structured_output", passed=False, certain=False, detail="could not tell"),
        ],
    )
    checker = PreflightChecker(
        "grid_ctf", settings=_settings(monkeypatch, AUTOCONTEXT_AGENT_PROVIDER="ollama")
    )
    results = checker.check_endpoint()
    assert [r.passed for r in results] == [True, False]
    assert PreflightChecker.blocking_failures(results) == []


def test_a_certain_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.endpoint_probe import ProbeResult
    from autocontext.preflight import PreflightChecker

    monkeypatch.setattr(
        "autocontext.endpoint_probe.probe_endpoint",
        lambda *_: [ProbeResult(name="endpoint_reachable", passed=False, certain=True, detail="refused")],
    )
    results = PreflightChecker("grid_ctf", settings=_settings(monkeypatch, AUTOCONTEXT_AGENT_PROVIDER="ollama")).check_endpoint()
    assert len(PreflightChecker.blocking_failures(results)) == 1


def test_the_run_command_actually_calls_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The half of AC-914 that is easy to forget.

    Endpoint probes added to a checker nobody invokes would compile, pass their
    own tests, and protect nothing -- which is exactly what PreflightChecker
    did for its whole life before this change.
    """
    from autocontext import cli

    called: list[str] = []
    monkeypatch.setattr(cli, "run_preflight", lambda scenario, preset, **_: (called.append(scenario), [])[1])
    monkeypatch.setattr(cli, "_is_agent_task", lambda _: True)
    monkeypatch.setattr(cli, "_run_agent_task", lambda *a, **k: pytest.skip("agent task path not under test"))

    with pytest.raises((typer.Exit, BaseException)):
        cli.run(scenario_text="grid_ctf", scenario="", gens=1, iterations=None, run_id=None,
                serve=False, port=8000, preset=None, json_output=True, skip_preflight=False)
    assert called == ["grid_ctf"]


def test_skip_preflight_skips_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext import cli

    called: list[str] = []
    monkeypatch.setattr(cli, "run_preflight", lambda scenario, preset, **_: (called.append(scenario), [])[1])
    monkeypatch.setattr(cli, "_is_agent_task", lambda _: True)
    monkeypatch.setattr(cli, "_run_agent_task", lambda *a, **k: pytest.skip("agent task path not under test"))

    with pytest.raises((typer.Exit, BaseException)):
        cli.run(scenario_text="grid_ctf", scenario="", gens=1, iterations=None, run_id=None,
                serve=False, port=8000, preset=None, json_output=True, skip_preflight=True)
    assert called == []
