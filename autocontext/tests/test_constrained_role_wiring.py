"""AC-913: the schema actually reaches the backend, end to end.

The point of these tests is that the capability FIRES. An earlier revision
dispatched to the constrained path only when ``task.system`` was empty -- and
every real call site passes a system prompt (structural isolation, ERP-67), so
the feature compiled, typechecked, passed its unit tests, and would never have
run once in production. That class of bug is invisible to a test that calls
the schema helpers directly, so these go through the real runner.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema


class _RecordingProvider(LLMProvider):
    """Records what the bridge actually sent, and can pretend to enforce it."""

    def __init__(self, *, enforce: bool, text: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._enforce = enforce
        self._text = text

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "output_schema": output_schema,
            }
        )
        return CompletionResult(text=self._text, model="stub", constrained=self._enforce)

    def default_model(self) -> str:
        return "stub"


class _LegacyProvider(LLMProvider):
    """Public provider implementation written before output_schema existed."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text=(
                "## Findings\n\n- legacy finding\n\n"
                "## Root Causes\n\n- legacy cause\n\n"
                "## Actionable Recommendations\n\n- legacy recommendation"
            ),
            model="legacy",
        )

    def default_model(self) -> str:
        return "legacy"


_VALID = json.dumps({"findings": ["f"], "root_causes": ["r"], "recommendations": ["rec"]})


def _run_analyst(provider: _RecordingProvider, *, system: str) -> Any:
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    runtime = SubagentRuntime(ProviderBridgeClient(provider))
    return AnalystRunner(runtime, model="stub").run("analyze this", system=system)


def test_schema_reaches_the_provider_even_with_a_system_prompt() -> None:
    """The regression that motivated this file.

    Every production call site passes system=; if the schema only rode the
    no-system path, constrained decoding would never happen in a real run.
    """
    provider = _RecordingProvider(enforce=True, text=_VALID)
    _run_analyst(provider, system="you are the analyst")

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["output_schema"] is not None, "schema never reached the provider"
    assert call["output_schema"].name == "analyst_output"
    # The system turn must survive the constrained path, or role isolation
    # silently weakens for exactly the calls that asked for a schema.
    assert call["system_prompt"] == "you are the analyst"


def test_schema_reaches_the_provider_without_a_system_prompt() -> None:
    provider = _RecordingProvider(enforce=True, text=_VALID)
    _run_analyst(provider, system="")

    assert provider.calls[0]["output_schema"] is not None


def test_schema_survives_the_production_hook_wrapper() -> None:
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.extensions import HookBus, HookedLanguageModelClient
    from autocontext.harness.core.subagent import SubagentRuntime

    provider = _RecordingProvider(enforce=True, text=_VALID)
    client = HookedLanguageModelClient(ProviderBridgeClient(provider), HookBus())
    execution = AnalystRunner(SubagentRuntime(client), model="stub").run(
        "analyze this",
        system="you are the analyst",
    )

    assert client.supports_constrained_output is True
    assert provider.calls[0]["output_schema"].name == "analyst_output"
    assert provider.calls[0]["system_prompt"] == "you are the analyst"
    assert execution.metadata["constrained"] is True


def test_legacy_provider_omits_the_new_keyword_and_runs_unconstrained() -> None:
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.parsers import parse_analyst_exec
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    provider = _LegacyProvider()
    bridge = ProviderBridgeClient(provider)
    execution = AnalystRunner(SubagentRuntime(bridge), model="legacy").run("analyze")

    assert bridge.supports_constrained_output is False
    assert provider.calls == 1
    assert execution.metadata["constrained"] is False
    assert parse_analyst_exec(execution).findings == ["legacy finding"]


def test_execution_records_whether_the_backend_enforced() -> None:
    """AC-913 criterion 3: the run record says whether output was constrained."""
    enforced = _run_analyst(_RecordingProvider(enforce=True, text=_VALID), system="s")
    assert enforced.metadata["constrained"] is True

    ignored = _run_analyst(_RecordingProvider(enforce=False, text="## Findings\n\n- f"), system="s")
    assert ignored.metadata["constrained"] is False


def test_parse_follows_what_the_backend_actually_did() -> None:
    """Enforced output is validated; ignored output keeps the old scrape path."""
    from autocontext.agents.parsers import parse_analyst_exec

    enforced = _run_analyst(_RecordingProvider(enforce=True, text=_VALID), system="s")
    assert parse_analyst_exec(enforced).findings == ["f"]

    markdown = "## Findings\n\n- from markdown\n\n## Root Causes\n\n- rc\n\n## Actionable Recommendations\n\n- rec"
    ignored = _run_analyst(_RecordingProvider(enforce=False, text=markdown), system="s")
    assert parse_analyst_exec(ignored).findings == ["from markdown"]


def test_a_backend_that_ignores_the_schema_still_produces_a_run() -> None:
    """AC-913 criterion 3's other half: unsupported backends keep working.

    The provider here reports constrained=False and returns prose. Nothing
    raises; the scrape path handles it exactly as before this feature existed.
    """
    from autocontext.agents.parsers import parse_analyst_exec

    execution = _run_analyst(_RecordingProvider(enforce=False, text="just some prose"), system="s")
    result = parse_analyst_exec(execution)
    assert result.findings == []
    assert execution.metadata["constrained"] is False


def test_normal_output_assembly_uses_validated_rendered_views() -> None:
    from autocontext.agents.orchestrator_helpers import _assemble_agent_outputs
    from autocontext.harness.core.types import RoleExecution, RoleUsage

    def execution(role: str, content: str, *, constrained: bool = False) -> RoleExecution:
        return RoleExecution(
            role=role,
            content=content,
            usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
            subagent_id=role,
            status="completed",
            metadata={"constrained": constrained},
        )

    architect_json = json.dumps(
        {
            "observed_bottlenecks": ["slow checks"],
            "impact_hypothesis": "Faster feedback.",
            "tools": [{"name": "probe", "description": "d", "code": "def probe(): ..."}],
            "harness": [
                {
                    "name": "guard",
                    "description": "d",
                    "code": "def validate_strategy(strategy, scenario):\n    return True, []",
                }
            ],
            "mutations": [],
            "dag_changes": [],
            "tuning_parameters": [],
            "tuning_reasoning": "",
            "changelog_entry": "added probe",
        }
    )
    analyst = execution("analyst", _VALID, constrained=True)
    coach = execution(
        "coach",
        json.dumps({"playbook": "P", "lessons": "L", "hints": "H"}),
        constrained=True,
    )
    architect = execution("architect", architect_json, constrained=True)
    outputs = _assemble_agent_outputs(
        SimpleNamespace(settings=SimpleNamespace(code_strategies_enabled=False)),
        '{"move":"north"}',
        {"move": "north"},
        execution("competitor", '{"move":"north"}'),
        execution("translator", '{"move":"north"}'),
        analyst,
        coach,
        architect,
    )

    assert outputs.analysis_markdown.startswith("## Findings")
    assert outputs.coach_markdown.startswith("<!-- PLAYBOOK_START -->")
    assert outputs.coach_playbook == "P"
    assert outputs.coach_lessons == "L"
    assert outputs.coach_competitor_hints == "H"
    assert outputs.architect_markdown.startswith("## Observed Bottlenecks")
    assert [item["name"] for item in outputs.architect_tools] == ["probe"]
    assert [item["name"] for item in outputs.architect_harness_specs] == ["guard"]


def test_escape_hatch_stops_the_schema_reaching_the_provider() -> None:
    """AC-931: off means no schema on the wire, not merely "we meant not to".

    Asserts what the provider RECEIVED. The AC-913 near-miss was a dispatch
    condition that looked right and never fired; the only defence is checking
    the request itself.
    """
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    provider = _RecordingProvider(enforce=True, text=_VALID)
    runtime = SubagentRuntime(ProviderBridgeClient(provider), constrained_output=False)
    execution = AnalystRunner(runtime, model="stub").run("analyze this", system="s")

    assert provider.calls[0]["output_schema"] is None
    # And the run records the truth: nothing was enforced.
    assert execution.metadata["constrained"] is False


def test_escape_hatch_output_still_parses_via_markdown() -> None:
    """Turning it off must not create a new failure mode, just the old one."""
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.parsers import parse_analyst_exec
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    markdown = "## Findings\n\n- a\n\n## Root Causes\n\n- b\n\n## Actionable Recommendations\n\n- c"
    runtime = SubagentRuntime(
        ProviderBridgeClient(_RecordingProvider(enforce=False, text=markdown)),
        constrained_output=False,
    )
    result = parse_analyst_exec(AnalystRunner(runtime, model="stub").run("p", system="s"))
    assert result.findings == ["a"]


def test_default_is_on_so_the_improvement_is_the_default() -> None:
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    provider = _RecordingProvider(enforce=True, text=_VALID)
    AnalystRunner(SubagentRuntime(ProviderBridgeClient(provider)), model="stub").run("p", system="s")
    assert provider.calls[0]["output_schema"] is not None
