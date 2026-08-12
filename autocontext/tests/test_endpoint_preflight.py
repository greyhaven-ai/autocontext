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
from pathlib import Path
from typing import Any

import pytest


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
    assert resolved == ("http://localhost:11434/v1", "ollama", "llama3.1:8b")


def test_agent_probe_reuses_runtime_base_url_and_provider_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.endpoint_probe import resolve_agent_endpoint

    resolved = resolve_agent_endpoint(
        _settings(
            monkeypatch,
            AUTOCONTEXT_AGENT_PROVIDER="openrouter",
            AUTOCONTEXT_AGENT_API_KEY="local-secret",
            AUTOCONTEXT_JUDGE_BASE_URL="http://127.0.0.1:9999/v1",
        )
    )
    assert resolved == ("http://127.0.0.1:9999/v1", "local-secret", "anthropic/claude-sonnet-5")


def test_default_base_urls_match_the_provider_registry() -> None:
    """Probe and provider construction consume one canonical URL table."""
    from autocontext.config.settings import AppSettings
    from autocontext.endpoint_probe import resolve_agent_endpoint
    from autocontext.providers.registry import resolve_provider_base_url

    for transport in ("openai", "openai-compatible", "ollama", "vllm", "openrouter"):
        # AppSettings construction avoids environment-dependent API-key lookup;
        # this assertion is about the shared destination only.
        resolved = resolve_agent_endpoint(AppSettings(agent_provider=transport, agent_api_key="test"))
        assert resolved is not None
        assert resolved[0] == resolve_provider_base_url(transport)


def test_all_explicit_run_endpoints_are_resolved() -> None:
    from autocontext.config.settings import AppSettings
    from autocontext.endpoint_probe import resolve_run_endpoints

    settings = AppSettings(
        agent_provider="anthropic",
        anthropic_api_key="anthropic-key",
        competitor_provider="ollama",
        competitor_base_url="http://competitor.test/v1",
        judge_provider="vllm",
        judge_base_url="http://judge.test/v1",
        judge_model="judge-model",
    )
    targets = {target.name: target for target in resolve_run_endpoints(settings)}

    assert targets["competitor"].base_url == "http://competitor.test/v1"
    assert targets["competitor"].model == "llama3.1"
    assert targets["judge"].base_url == "http://judge.test/v1"
    assert targets["judge"].model == "judge-model"


def test_identical_run_endpoints_are_probed_once() -> None:
    from autocontext.config.settings import AppSettings
    from autocontext.endpoint_probe import resolve_run_endpoints

    settings = AppSettings(
        agent_provider="ollama",
        competitor_provider="ollama",
        judge_provider="ollama",
        judge_model="llama3.1",
    )
    targets = resolve_run_endpoints(settings)

    assert [(target.name, target.model) for target in targets] == [("agent", "llama3.1")]


@pytest.mark.parametrize("exc", [TimeoutError("slow"), pytest.param(None, id="http-429")])
def test_transient_reachability_failures_are_uncertain(monkeypatch: pytest.MonkeyPatch, exc: BaseException | None) -> None:
    from urllib.error import HTTPError

    from autocontext.endpoint_probe import probe_reachable

    failure = exc or HTTPError("http://endpoint.test/v1/models", 429, "rate limited", None, None)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr("autocontext.endpoint_probe._get_json", fail)

    result = probe_reachable("http://endpoint.test/v1", "key")
    assert not result.passed
    assert not result.certain


def test_connection_refused_is_a_certain_dead_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import URLError

    from autocontext.endpoint_probe import probe_reachable

    failure = URLError(ConnectionRefusedError("refused"))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr("autocontext.endpoint_probe._get_json", fail)

    result = probe_reachable("http://endpoint.test/v1", "key")
    assert not result.passed
    assert result.certain


@pytest.mark.parametrize("content", ['{"wrong": 1}', '{"ok": 1}'])
def test_structured_output_must_match_the_requested_schema(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    from autocontext.endpoint_probe import probe_structured_output

    monkeypatch.setattr(
        "autocontext.endpoint_probe._get_json",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": content}}]},
    )
    result = probe_structured_output("http://endpoint.test/v1", "key", "model")
    assert not result.passed
    assert not result.certain


def test_structured_output_passes_only_for_the_exact_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.endpoint_probe import probe_structured_output

    monkeypatch.setattr(
        "autocontext.endpoint_probe._get_json",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": '{"ok": true}'}}]},
    )
    result = probe_structured_output("http://endpoint.test/v1", "key", "model")
    assert result.passed
    assert result.certain


def test_malformed_model_list_is_uncertain_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.endpoint_probe import probe_model_served

    monkeypatch.setattr("autocontext.endpoint_probe._get_json", lambda *_: [])
    result = probe_model_served("http://endpoint.test/v1", "key", "model")
    assert not result.passed
    assert not result.certain


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


def test_endpoint_exception_does_not_erase_static_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from autocontext.config.settings import AppSettings
    from autocontext.endpoint_probe import EndpointTarget
    from autocontext.preflight import PreflightChecker

    target = EndpointTarget("agent", "ollama", "http://endpoint.test/v1", "ollama", "llama3.1")
    monkeypatch.setattr("autocontext.endpoint_probe.resolve_run_endpoints", lambda _settings: [target])

    def crash(*_args: object, **_kwargs: object) -> None:
        raise AttributeError("malformed response")

    monkeypatch.setattr("autocontext.endpoint_probe.probe_endpoint", crash)
    results = PreflightChecker(
        "definitely-not-a-scenario",
        knowledge_root=tmp_path,
        settings=AppSettings(agent_provider="ollama"),
    ).run_all()

    scenario = next(result for result in results if result.name == "scenario_exists")
    endpoint = next(result for result in results if result.name == "agent.endpoint_probe")
    assert not scenario.passed and scenario.blocking
    assert not endpoint.passed and not endpoint.blocking


def test_scenario_check_uses_the_configured_knowledge_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from autocontext.preflight import PreflightChecker

    captured: list[object] = []
    monkeypatch.setattr(
        "autocontext.preflight.resolve_scenario_class",
        lambda name, root: captured.append((name, root)) or object,
    )

    result = PreflightChecker("persisted-custom", knowledge_root=tmp_path).check_scenario_exists()
    assert result.passed
    assert captured == [("persisted-custom", tmp_path)]


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
    monkeypatch.setattr(
        cli,
        "_run_agent_task",
        lambda *a, **k: cli.AgentTaskRunSummary("run", "grid_ctf", 1.0, "ok", 1, True, "threshold_met"),
    )

    cli.run(scenario_text="grid_ctf", scenario="", gens=1, iterations=None, run_id=None,
            serve=False, port=8000, preset=None, json_output=True, skip_preflight=False)
    assert called == ["grid_ctf"]


def test_skip_preflight_skips_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext import cli

    called: list[str] = []
    monkeypatch.setattr(cli, "run_preflight", lambda scenario, preset, **_: (called.append(scenario), [])[1])
    monkeypatch.setattr(cli, "_is_agent_task", lambda _: True)
    monkeypatch.setattr(
        cli,
        "_run_agent_task",
        lambda *a, **k: cli.AgentTaskRunSummary("run", "grid_ctf", 1.0, "ok", 1, True, "threshold_met"),
    )

    cli.run(scenario_text="grid_ctf", scenario="", gens=1, iterations=None, run_id=None,
            serve=False, port=8000, preset=None, json_output=True, skip_preflight=True)
    assert called == []
