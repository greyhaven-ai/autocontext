"""Tests for the multi-model provider abstraction."""

from __future__ import annotations

import json

import pytest

from mts.execution.judge import JudgeResult, LLMJudge
from mts.providers.base import CompletionResult, LLMProvider, ProviderError
from mts.providers.callable_wrapper import CallableProvider
from mts.providers.registry import create_provider

try:
    import openai  # noqa: F401

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

_skip_no_openai = pytest.mark.skipif(not _HAS_OPENAI, reason="openai package not installed")

# ---------------------------------------------------------------------------
# Base interface tests
# ---------------------------------------------------------------------------

class _DummyProvider(LLMProvider):
    """Concrete test provider."""

    def __init__(self, response: str = "hello") -> None:
        self._response = response
        self.calls: list[dict] = []

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        self.calls.append({
            "system": system_prompt,
            "user": user_prompt,
            "model": model,
            "temperature": temperature,
        })
        return CompletionResult(text=self._response, model=model or "dummy")

    def default_model(self):
        return "dummy-v1"


class TestLLMProviderInterface:
    def test_concrete_provider_works(self):
        p = _DummyProvider("test response")
        result = p.complete("sys", "usr")
        assert result.text == "test response"
        assert result.model == "dummy"
        assert len(p.calls) == 1

    def test_default_model(self):
        p = _DummyProvider()
        assert p.default_model() == "dummy-v1"

    def test_name_property(self):
        p = _DummyProvider()
        assert p.name == "_DummyProvider"

    def test_completion_result_fields(self):
        r = CompletionResult(text="hi", model="m", usage={"input_tokens": 10}, cost_usd=0.01)
        assert r.text == "hi"
        assert r.model == "m"
        assert r.usage["input_tokens"] == 10
        assert r.cost_usd == 0.01

    def test_completion_result_defaults(self):
        r = CompletionResult(text="hi")
        assert r.model is None
        assert r.usage == {}
        assert r.cost_usd is None


# ---------------------------------------------------------------------------
# CallableProvider (backward compat wrapper)
# ---------------------------------------------------------------------------

class TestCallableProvider:
    def test_wraps_callable(self):
        def fn(sys: str, usr: str) -> str:
            return f"echo: {usr}"

        p = CallableProvider(fn, model_name="test-model")
        result = p.complete("sys", "hello")
        assert result.text == "echo: hello"
        assert result.model == "test-model"

    def test_default_model(self):
        p = CallableProvider(lambda s, u: "", model_name="my-model")
        assert p.default_model() == "my-model"

    def test_error_wrapping(self):
        def bad_fn(s, u):
            raise RuntimeError("boom")

        p = CallableProvider(bad_fn)
        with pytest.raises(ProviderError, match="boom"):
            p.complete("sys", "usr")


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_create_anthropic_provider(self):
        # Just test that it creates without error (won't call API)
        p = create_provider("anthropic", api_key="test-key", model="claude-test")
        assert p.default_model() == "claude-test"
        assert p.name == "AnthropicProvider"

    @_skip_no_openai
    def test_create_ollama_provider(self):
        p = create_provider("ollama", model="llama3.1")
        assert p.default_model() == "llama3.1"
        assert p.name == "OpenAICompatibleProvider"

    @_skip_no_openai
    def test_create_vllm_provider(self):
        p = create_provider("vllm", base_url="http://gpu-box:8000/v1", model="mistral-7b")
        assert p.default_model() == "mistral-7b"

    def test_unknown_provider_raises(self):
        with pytest.raises(ProviderError, match="Unknown provider type"):
            create_provider("magic-llm")

    def test_case_insensitive(self):
        p = create_provider("ANTHROPIC", api_key="test")
        assert p.name == "AnthropicProvider"

    @_skip_no_openai
    def test_create_openai_compat(self):
        p = create_provider(
            "openai-compatible",
            api_key="sk-test",
            base_url="http://localhost:8080/v1",
            model="custom-model",
        )
        assert p.default_model() == "custom-model"


# ---------------------------------------------------------------------------
# LLMJudge with provider
# ---------------------------------------------------------------------------

def _make_judge_response(score: float = 0.75, reasoning: str = "good", dims: dict | None = None) -> str:
    data = {"score": score, "reasoning": reasoning, "dimensions": dims or {}}
    return f"Some preamble\n<!-- JUDGE_RESULT_START -->\n{json.dumps(data)}\n<!-- JUDGE_RESULT_END -->\nTrailing text"


class TestJudgeWithProvider:
    def test_judge_with_provider(self):
        provider = _DummyProvider(_make_judge_response(0.85, "excellent"))
        judge = LLMJudge(model="test", rubric="be good", provider=provider)
        result = judge.evaluate("write something", "here is output")
        assert isinstance(result, JudgeResult)
        assert result.score == 0.85
        assert "excellent" in result.reasoning

    def test_judge_with_llm_fn_backward_compat(self):
        def fn(sys: str, usr: str) -> str:
            return _make_judge_response(0.60, "okay")

        judge = LLMJudge(model="test", rubric="be good", llm_fn=fn)
        result = judge.evaluate("task", "output")
        assert result.score == 0.60

    def test_judge_requires_provider_or_fn(self):
        with pytest.raises(ValueError, match="Either 'provider' or 'llm_fn'"):
            LLMJudge(model="test", rubric="rubric")

    def test_judge_provider_takes_precedence(self):
        """When both provider and llm_fn are given, provider wins."""
        provider = _DummyProvider(_make_judge_response(0.99, "provider"))

        def fn(s: str, u: str) -> str:
            return _make_judge_response(0.01, "callable")

        judge = LLMJudge(model="test", rubric="rubric", provider=provider, llm_fn=fn)
        result = judge.evaluate("task", "output")
        assert result.score == 0.99
        assert "provider" in result.reasoning

    def test_judge_multi_sample_with_provider(self):
        provider = _DummyProvider(_make_judge_response(0.80, "good"))
        judge = LLMJudge(model="test", rubric="rubric", provider=provider, samples=3)
        result = judge.evaluate("task", "output")
        assert abs(result.score - 0.80) < 1e-9
        assert len(result.raw_responses) == 3
        assert len(provider.calls) == 3

    def test_judge_passes_model_to_provider(self):
        provider = _DummyProvider(_make_judge_response(0.70))
        judge = LLMJudge(model="custom-judge-model", rubric="rubric", provider=provider)
        judge.evaluate("task", "output")
        assert provider.calls[0]["model"] == "custom-judge-model"

    def test_judge_with_reference_context(self):
        provider = _DummyProvider(_make_judge_response(0.90, "accurate", {"factual_accuracy": 0.95}))
        judge = LLMJudge(model="test", rubric="rubric", provider=provider)
        result = judge.evaluate("task", "output", reference_context="RLM = Recursive Language Model")
        assert result.dimension_scores["factual_accuracy"] == 0.95
        assert "Reference Context" in provider.calls[0]["user"]

    def test_judge_with_calibration_examples(self):
        provider = _DummyProvider(_make_judge_response(0.88))
        judge = LLMJudge(model="test", rubric="rubric", provider=provider)
        calibration = [{"human_score": 0.9, "human_notes": "good", "agent_output": "test output"}]
        result = judge.evaluate("task", "output", calibration_examples=calibration)
        assert result.score == 0.88
        assert "Calibration Examples" in provider.calls[0]["user"]


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------

class TestSettingsIntegration:
    def test_new_settings_have_defaults(self):
        from mts.config.settings import AppSettings
        s = AppSettings()
        assert s.judge_provider == "anthropic"
        assert s.judge_base_url is None
        assert s.judge_api_key is None

    def test_get_provider_from_settings(self, monkeypatch):
        from mts.config.settings import AppSettings
        from mts.providers.registry import get_provider

        monkeypatch.setenv("MTS_JUDGE_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        settings = AppSettings(judge_model="claude-test")
        provider = get_provider(settings)
        assert provider.name == "AnthropicProvider"
        assert provider.default_model() == "claude-test"

    @_skip_no_openai
    def test_get_provider_ollama(self):
        from mts.config.settings import AppSettings
        from mts.providers.registry import get_provider

        settings = AppSettings(judge_provider="ollama", judge_model="llama3.1")
        provider = get_provider(settings)
        assert provider.name == "OpenAICompatibleProvider"
        assert provider.default_model() == "llama3.1"


# ---------------------------------------------------------------------------
# Fallback parser tests (MTS-12)
# ---------------------------------------------------------------------------

class TestJudgeFallbackParser:
    """Test all 4 parse strategies in LLMJudge._parse_judge_response."""

    def _make_judge(self):
        from mts.execution.judge import LLMJudge
        from mts.providers.callable_wrapper import CallableProvider

        provider = CallableProvider(lambda s, u: "mock", model_name="mock")
        return LLMJudge(provider=provider, model="mock", rubric="test rubric")

    def test_strategy1_markers(self):
        judge = self._make_judge()
        response = (
            'Some preamble text\n'
            '<!-- JUDGE_RESULT_START -->\n'
            '{"score": 0.85, "reasoning": "Good work", "dimensions": {"clarity": 0.9}}\n'
            '<!-- JUDGE_RESULT_END -->\n'
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.85
        assert "Good work" in reasoning
        assert dims["clarity"] == 0.9
        assert _pm == "raw_json"  # raw_json tried first, matches JSON inside markers

    def test_strategy2_code_block(self):
        judge = self._make_judge()
        response = (
            'Here is my evaluation:\n\n'
            '```json\n'
            '{"score": 0.72, "reasoning": "Decent attempt", "dimensions": {"insight": 0.7}}\n'
            '```\n'
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.72
        assert "Decent attempt" in reasoning
        assert _pm == "raw_json"  # raw_json tried first, matches JSON inside code block

    def test_strategy2_code_block_no_lang(self):
        judge = self._make_judge()
        response = (
            '```\n'
            '{"score": 0.65, "reasoning": "OK", "dimensions": {}}\n'
            '```\n'
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.65

    def test_strategy3_raw_json(self):
        judge = self._make_judge()
        response = (
            'I would rate this output as follows:\n\n'
            '{"score": 0.91, "reasoning": "Excellent", "dimensions": {"voice": 0.95, "brevity": 0.88}}\n\n'
            'Overall a strong piece.'
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.91
        assert dims["voice"] == 0.95
        assert reasoning == "Excellent"
        assert _pm == "raw_json"

    def test_strategy3_raw_json_nested(self):
        judge = self._make_judge()
        response = (
            'The evaluation:\n'
            '{"score": 0.80, "reasoning": "Good", "dimensions": {"a": 0.8, "b": 0.7}}'
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.80
        assert len(dims) == 2

    def test_strategy4_plaintext_score(self):
        judge = self._make_judge()
        response = (
            "This is a well-written piece that demonstrates strong voice and insight.\n\n"
            "Overall score: 0.82\n\n"
            "The main areas for improvement are brevity and specificity."
        )
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.82
        assert _pm == "plaintext"
        assert "[plaintext parse]" not in reasoning
        assert dims == {}  # No structured dimensions from plaintext

    def test_strategy4_quoted_score(self):
        judge = self._make_judge()
        response = 'The "score": 0.75 reflects moderate quality.'
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.75

    def test_strategy4_score_colon(self):
        judge = self._make_judge()
        response = "Score: 0.88\nReasoning: Very good output."
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.88

    def test_all_strategies_fail(self):
        judge = self._make_judge()
        response = "I think this is pretty good but I don't want to give a number."
        score, reasoning, dims, _pm = judge._parse_judge_response(response)
        assert score == 0.0
        assert "no parseable score" in reasoning

    def test_strategy_priority_raw_json_first(self):
        """Raw JSON is tried first — the first JSON object wins."""
        judge = self._make_judge()
        response = (
            '```json\n{"score": 0.50, "reasoning": "code block"}\n```\n'
            '<!-- JUDGE_RESULT_START -->\n'
            '{"score": 0.90, "reasoning": "markers"}\n'
            '<!-- JUDGE_RESULT_END -->'
        )
        score, reasoning, _, _pm = judge._parse_judge_response(response)
        assert score == 0.50
        assert reasoning == "code block"
        assert _pm == "raw_json"

    def test_score_clamping(self):
        judge = self._make_judge()
        response = '<!-- JUDGE_RESULT_START -->\n{"score": 1.5, "reasoning": "too high"}\n<!-- JUDGE_RESULT_END -->'
        score, _, _, _pm = judge._parse_judge_response(response)
        assert score == 1.0

    def test_score_clamping_negative(self):
        judge = self._make_judge()
        response = '<!-- JUDGE_RESULT_START -->\n{"score": -0.5, "reasoning": "negative"}\n<!-- JUDGE_RESULT_END -->'
        score, _, _, _pm = judge._parse_judge_response(response)
        assert score == 0.0
