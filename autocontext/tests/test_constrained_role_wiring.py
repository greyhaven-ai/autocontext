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
