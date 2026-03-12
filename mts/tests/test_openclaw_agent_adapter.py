"""Tests for AC-193: OpenClaw agent adapter for running agents inside MTS harness."""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mts.harness.core.types import ModelResponse, RoleUsage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trace(
    *,
    steps: int = 3,
    tool_calls: int = 2,
    output: str = "strategy output",
    model: str = "openclaw-agent-v1",
    input_tokens: int = 100,
    output_tokens: int = 50,
    latency_ms: int = 500,
) -> dict[str, Any]:
    """Build a minimal OpenClaw execution trace dict."""
    return {
        "output": output,
        "model": model,
        "steps": [
            {"type": "reasoning", "content": f"Step {i}", "duration_ms": 100}
            for i in range(steps)
        ],
        "tool_calls": [
            {"name": f"tool_{i}", "input": {"x": i}, "output": {"y": i * 2}, "duration_ms": 50}
            for i in range(tool_calls)
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "total_duration_ms": latency_ms,
    }


class _FactoryAgent:
    def execute(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _make_trace(output=f"factory:{prompt}", model=model)


def build_test_openclaw_agent(settings: Any) -> _FactoryAgent:
    del settings
    return _FactoryAgent()


# ---------------------------------------------------------------------------
# TestOpenClawExecutionTrace
# ---------------------------------------------------------------------------


class TestOpenClawExecutionTrace:
    def test_from_dict_parses_steps(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(_make_trace(steps=4, tool_calls=1))
        assert len(trace.steps) == 4
        assert len(trace.tool_calls) == 1

    def test_from_dict_captures_output_and_model(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(_make_trace(output="hello", model="m1"))
        assert trace.output == "hello"
        assert trace.model == "m1"

    def test_from_dict_captures_usage(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(
            _make_trace(input_tokens=200, output_tokens=80, latency_ms=1200),
        )
        assert trace.input_tokens == 200
        assert trace.output_tokens == 80
        assert trace.total_duration_ms == 1200

    def test_from_dict_empty_trace(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict({
            "output": "",
            "model": "",
            "steps": [],
            "tool_calls": [],
            "usage": {},
            "total_duration_ms": 0,
        })
        assert trace.output == ""
        assert trace.steps == []
        assert trace.tool_calls == []
        assert trace.input_tokens == 0

    def test_to_role_usage(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(
            _make_trace(input_tokens=150, output_tokens=60, latency_ms=800, model="agent-v2"),
        )
        usage = trace.to_role_usage()
        assert isinstance(usage, RoleUsage)
        assert usage.input_tokens == 150
        assert usage.output_tokens == 60
        assert usage.latency_ms == 800
        assert usage.model == "agent-v2"


# ---------------------------------------------------------------------------
# TestOpenClawAgentProtocol
# ---------------------------------------------------------------------------


class TestOpenClawAgentProtocol:
    def test_callable_agent_satisfies_protocol(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawAgentProtocol

        class MyAgent:
            def execute(
                self,
                *,
                prompt: str,
                model: str,
                max_tokens: int,
                temperature: float,
                tools: list[dict[str, Any]] | None = None,
            ) -> dict[str, Any]:
                return _make_trace(output="done")

        agent = MyAgent()
        assert isinstance(agent, OpenClawAgentProtocol)
        result = agent.execute(prompt="test", model="m", max_tokens=100, temperature=0.0)
        assert result["output"] == "done"


# ---------------------------------------------------------------------------
# TestOpenClawClient
# ---------------------------------------------------------------------------


class TestOpenClawClient:
    def _make_client(
        self,
        agent: Any = None,
        *,
        max_retries: int = 0,
        timeout_seconds: float = 30.0,
    ) -> Any:
        from mts.openclaw.agent_adapter import OpenClawClient

        if agent is None:
            agent = MagicMock()
            agent.execute.return_value = _make_trace()
        return OpenClawClient(
            agent=agent,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
        )

    def test_generate_returns_model_response(self) -> None:
        agent = MagicMock()
        agent.execute.return_value = _make_trace(output="strategy json")
        client = self._make_client(agent)

        response = client.generate(
            model="openclaw-v1",
            prompt="Generate a strategy",
            max_tokens=800,
            temperature=0.2,
            role="competitor",
        )

        assert isinstance(response, ModelResponse)
        assert response.text == "strategy json"
        assert response.usage.model == "openclaw-agent-v1"

    def test_generate_passes_prompt_and_params(self) -> None:
        agent = MagicMock()
        agent.execute.return_value = _make_trace()
        client = self._make_client(agent)

        client.generate(
            model="my-model",
            prompt="Do something",
            max_tokens=500,
            temperature=0.5,
            role="analyst",
        )

        agent.execute.assert_called_once_with(
            prompt="Do something",
            model="my-model",
            max_tokens=500,
            temperature=0.5,
            tools=None,
        )

    def test_generate_captures_usage(self) -> None:
        agent = MagicMock()
        agent.execute.return_value = _make_trace(
            input_tokens=300, output_tokens=120, latency_ms=1500,
        )
        client = self._make_client(agent)

        response = client.generate(
            model="m", prompt="p", max_tokens=100, temperature=0.0,
        )

        assert response.usage.input_tokens == 300
        assert response.usage.output_tokens == 120
        assert response.usage.latency_ms == 1500

    def test_generate_multiturn(self) -> None:
        agent = MagicMock()
        agent.execute.return_value = _make_trace(output="multiturn result")
        client = self._make_client(agent)

        response = client.generate_multiturn(
            model="m",
            system="You are an analyst.",
            messages=[
                {"role": "user", "content": "Analyze this"},
                {"role": "assistant", "content": "I see patterns"},
                {"role": "user", "content": "What patterns?"},
            ],
            max_tokens=1000,
            temperature=0.3,
            role="analyst",
        )

        assert isinstance(response, ModelResponse)
        assert response.text == "multiturn result"
        # Should pass combined prompt
        call_kwargs = agent.execute.call_args
        prompt = call_kwargs.kwargs["prompt"] if call_kwargs.kwargs else call_kwargs[1]["prompt"]
        assert "You are an analyst." in prompt
        assert "What patterns?" in prompt

    def test_stores_last_trace(self) -> None:
        agent = MagicMock()
        agent.execute.return_value = _make_trace(steps=5, tool_calls=3)
        client = self._make_client(agent)

        client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)

        assert client.last_trace is not None
        assert len(client.last_trace.steps) == 5
        assert len(client.last_trace.tool_calls) == 3


# ---------------------------------------------------------------------------
# TestRetryBehavior
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    def test_retries_on_failure(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawClient

        agent = MagicMock()
        agent.execute.side_effect = [
            RuntimeError("timeout"),
            _make_trace(output="success after retry"),
        ]
        client = OpenClawClient(agent=agent, max_retries=2, timeout_seconds=30.0)

        response = client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)

        assert response.text == "success after retry"
        assert agent.execute.call_count == 2

    def test_exhausts_retries_then_raises(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawAdapterError, OpenClawClient

        agent = MagicMock()
        agent.execute.side_effect = RuntimeError("always fails")
        client = OpenClawClient(agent=agent, max_retries=2, timeout_seconds=30.0)

        with pytest.raises(OpenClawAdapterError, match="after 3 attempts"):
            client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)

        assert agent.execute.call_count == 3  # 1 initial + 2 retries

    def test_no_retry_when_max_retries_zero(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawAdapterError, OpenClawClient

        agent = MagicMock()
        agent.execute.side_effect = RuntimeError("fail")
        client = OpenClawClient(agent=agent, max_retries=0, timeout_seconds=30.0)

        with pytest.raises(OpenClawAdapterError):
            client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)

        assert agent.execute.call_count == 1

    def test_retry_uses_backoff(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawClient

        agent = MagicMock()
        agent.execute.side_effect = [
            RuntimeError("fail1"),
            RuntimeError("fail2"),
            _make_trace(output="ok"),
        ]
        client = OpenClawClient(
            agent=agent, max_retries=3, timeout_seconds=30.0, retry_base_delay=0.01,
        )

        t0 = time.monotonic()
        client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)
        elapsed = time.monotonic() - t0

        # Should have some delay from backoff (at least 0.01 + 0.02 = 0.03s)
        assert elapsed >= 0.02


# ---------------------------------------------------------------------------
# TestTimeoutBehavior
# ---------------------------------------------------------------------------


class TestTimeoutBehavior:
    def test_timeout_raises_adapter_error(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawAdapterError, OpenClawClient

        def slow_execute(**kwargs: Any) -> dict[str, Any]:
            time.sleep(1.0)
            return _make_trace()

        agent = MagicMock()
        agent.execute.side_effect = slow_execute
        client = OpenClawClient(agent=agent, max_retries=0, timeout_seconds=0.05)

        with pytest.raises(OpenClawAdapterError, match="timed out"):
            client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)

    def test_timeout_returns_promptly(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawAdapterError, OpenClawClient

        def slow_execute(**kwargs: Any) -> dict[str, Any]:
            time.sleep(1.0)
            return _make_trace()

        agent = MagicMock()
        agent.execute.side_effect = slow_execute
        client = OpenClawClient(agent=agent, max_retries=0, timeout_seconds=0.05)

        t0 = time.monotonic()
        with pytest.raises(OpenClawAdapterError, match="timed out"):
            client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5


# ---------------------------------------------------------------------------
# TestTraceToEvaluationRecord
# ---------------------------------------------------------------------------


class TestTraceToEvaluationRecord:
    def test_trace_summary_for_evaluation(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(_make_trace(steps=3, tool_calls=2))
        summary = trace.to_evaluation_summary()

        assert "steps" in summary
        assert summary["steps"] == 3
        assert summary["tool_calls"] == 2
        assert "input_tokens" in summary
        assert "output_tokens" in summary
        assert "total_duration_ms" in summary

    def test_trace_to_role_execution(self) -> None:
        from mts.openclaw.agent_adapter import OpenClawExecutionTrace

        trace = OpenClawExecutionTrace.from_dict(
            _make_trace(output="result text", model="agent-v1", latency_ms=600),
        )
        role_exec = trace.to_role_execution(role="competitor")

        assert role_exec.role == "competitor"
        assert role_exec.content == "result text"
        assert role_exec.status == "completed"
        assert role_exec.usage.model == "agent-v1"
        assert role_exec.usage.latency_ms == 600
        assert "openclaw-" in role_exec.subagent_id


# ---------------------------------------------------------------------------
# TestProviderBridgeRegistration
# ---------------------------------------------------------------------------


class TestProviderBridgeRegistration:
    def test_openclaw_provider_creates_client(self) -> None:
        from mts.agents.provider_bridge import create_role_client

        settings = MagicMock()
        settings.openclaw_agent_factory = "test_openclaw_agent_adapter:build_test_openclaw_agent"
        settings.openclaw_timeout_seconds = 30.0
        settings.openclaw_max_retries = 2
        settings.openclaw_retry_base_delay = 0.25

        client = create_role_client("openclaw", settings)

        assert client is not None
        from mts.openclaw.agent_adapter import OpenClawClient

        assert isinstance(client, OpenClawClient)
        response = client.generate(model="agent-model", prompt="ping", max_tokens=32, temperature=0.0)
        assert response.text == "factory:ping"

    def test_openclaw_provider_case_insensitive(self) -> None:
        from mts.agents.provider_bridge import create_role_client

        settings = MagicMock()
        settings.openclaw_agent_factory = "test_openclaw_agent_adapter:build_test_openclaw_agent"
        settings.openclaw_timeout_seconds = 30.0
        settings.openclaw_max_retries = 2
        settings.openclaw_retry_base_delay = 0.25

        client = create_role_client("OpenClaw", settings)

        assert client is not None

    def test_openclaw_provider_requires_factory_setting(self) -> None:
        from mts.agents.provider_bridge import create_role_client

        settings = MagicMock()
        settings.openclaw_agent_factory = ""
        settings.openclaw_timeout_seconds = 30.0
        settings.openclaw_max_retries = 2
        settings.openclaw_retry_base_delay = 0.25

        with pytest.raises(ValueError, match="MTS_OPENCLAW_AGENT_FACTORY"):
            create_role_client("openclaw", settings)


# ---------------------------------------------------------------------------
# TestOpenClawSettings
# ---------------------------------------------------------------------------


class TestOpenClawSettings:
    def test_settings_have_openclaw_fields(self) -> None:
        from mts.config.settings import AppSettings

        s = AppSettings()
        assert hasattr(s, "openclaw_agent_factory")
        assert hasattr(s, "openclaw_timeout_seconds")
        assert hasattr(s, "openclaw_max_retries")
        assert hasattr(s, "openclaw_retry_base_delay")
        assert s.openclaw_agent_factory == ""
        assert s.openclaw_timeout_seconds == 30.0
        assert s.openclaw_max_retries == 2
        assert s.openclaw_retry_base_delay == 0.25

    def test_settings_from_env(self) -> None:
        import os

        from mts.config.settings import load_settings

        env = {
            "MTS_OPENCLAW_AGENT_FACTORY": "test_openclaw_agent_adapter:build_test_openclaw_agent",
            "MTS_OPENCLAW_TIMEOUT_SECONDS": "60.0",
            "MTS_OPENCLAW_MAX_RETRIES": "5",
            "MTS_OPENCLAW_RETRY_BASE_DELAY": "0.5",
        }
        with patch.dict(os.environ, env, clear=False):
            s = load_settings()

        assert s.openclaw_agent_factory == "test_openclaw_agent_adapter:build_test_openclaw_agent"
        assert s.openclaw_timeout_seconds == 60.0
        assert s.openclaw_max_retries == 5
        assert s.openclaw_retry_base_delay == 0.5
