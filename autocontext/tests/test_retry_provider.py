"""Tests for RetryProvider — provider error recovery (MTS-15)."""

from __future__ import annotations

import time

import pytest

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError
from autocontext.providers.retry import RetryProvider, _is_transient


class FakeProvider(LLMProvider):
    """Provider that fails N times then succeeds."""

    def __init__(self, fail_count: int = 0, error_msg: str = "rate limit exceeded"):
        self._fail_count = fail_count
        self._error_msg = error_msg
        self.call_count = 0

    def complete(self, system_prompt, user_prompt, **kwargs) -> CompletionResult:
        self.call_count += 1
        if self.call_count <= self._fail_count:
            raise ProviderError(self._error_msg)
        return CompletionResult(text="success", model="fake")

    def default_model(self) -> str:
        return "fake-model"


class TestIsTransient:
    def test_rate_limit(self):
        assert _is_transient(ProviderError("Rate limit exceeded"))

    def test_429(self):
        assert _is_transient(ProviderError("HTTP 429 Too Many Requests"))

    def test_timeout(self):
        assert _is_transient(ProviderError("Request timed out"))

    def test_server_error_500(self):
        assert _is_transient(ProviderError("500 Internal Server Error"))

    def test_502(self):
        assert _is_transient(ProviderError("502 Bad Gateway"))

    def test_503(self):
        assert _is_transient(ProviderError("503 Service Temporarily Unavailable"))

    def test_overloaded(self):
        assert _is_transient(ProviderError("API is overloaded"))

    def test_connection(self):
        assert _is_transient(ProviderError("Connection reset by peer"))

    def test_not_transient(self):
        assert not _is_transient(ProviderError("Invalid API key"))

    def test_not_transient_auth(self):
        assert not _is_transient(ProviderError("Authentication failed"))


class TestRetryProvider:
    def test_success_no_retry(self):
        inner = FakeProvider(fail_count=0)
        provider = RetryProvider(inner, max_retries=3, base_delay=0.001)
        result = provider.complete("sys", "user")
        assert result.text == "success"
        assert inner.call_count == 1

    def test_retry_on_transient_error(self):
        inner = FakeProvider(fail_count=2, error_msg="rate limit exceeded")
        provider = RetryProvider(inner, max_retries=3, base_delay=0.001)
        result = provider.complete("sys", "user")
        assert result.text == "success"
        assert inner.call_count == 3  # 2 failures + 1 success

    def test_exhaust_retries(self):
        inner = FakeProvider(fail_count=10, error_msg="rate limit exceeded")
        provider = RetryProvider(inner, max_retries=2, base_delay=0.001)
        with pytest.raises(ProviderError, match="rate limit"):
            provider.complete("sys", "user")
        assert inner.call_count == 3  # 1 initial + 2 retries

    def test_no_retry_on_non_transient(self):
        inner = FakeProvider(fail_count=5, error_msg="Invalid API key")
        provider = RetryProvider(inner, max_retries=3, base_delay=0.001)
        with pytest.raises(ProviderError, match="Invalid API key"):
            provider.complete("sys", "user")
        assert inner.call_count == 1  # No retries

    def test_retry_all_flag(self):
        inner = FakeProvider(fail_count=2, error_msg="Invalid API key")
        provider = RetryProvider(inner, max_retries=3, base_delay=0.001, retry_all=True)
        result = provider.complete("sys", "user")
        assert result.text == "success"
        assert inner.call_count == 3

    def test_backoff_increases_delay(self):
        inner = FakeProvider(fail_count=3, error_msg="timeout")
        provider = RetryProvider(
            inner, max_retries=3, base_delay=0.01,
            backoff_factor=2.0, max_delay=10.0,
        )
        start = time.monotonic()
        result = provider.complete("sys", "user")
        elapsed = time.monotonic() - start
        assert result.text == "success"
        # base=0.01, then 0.02, then 0.04 = 0.07s minimum
        assert elapsed >= 0.05

    def test_max_delay_cap(self):
        inner = FakeProvider(fail_count=3, error_msg="timeout")
        provider = RetryProvider(
            inner, max_retries=3, base_delay=0.01,
            backoff_factor=100.0, max_delay=0.02,
        )
        start = time.monotonic()
        provider.complete("sys", "user")
        elapsed = time.monotonic() - start
        # Should be capped: 0.01 + 0.02 + 0.02 = 0.05s max
        assert elapsed < 0.2

    def test_zero_retries(self):
        inner = FakeProvider(fail_count=1, error_msg="timeout")
        provider = RetryProvider(inner, max_retries=0, base_delay=0.001)
        with pytest.raises(ProviderError):
            provider.complete("sys", "user")
        assert inner.call_count == 1

    def test_default_model_delegates(self):
        inner = FakeProvider()
        provider = RetryProvider(inner)
        assert provider.default_model() == "fake-model"

    def test_name_wraps(self):
        inner = FakeProvider()
        provider = RetryProvider(inner)
        assert "Retry" in provider.name
        assert "FakeProvider" in provider.name

    def test_passes_kwargs(self):
        """Ensure model, temperature, max_tokens are forwarded."""
        calls = []

        class TrackingProvider(LLMProvider):
            def complete(self, system_prompt, user_prompt, **kwargs):
                calls.append(kwargs)
                return CompletionResult(text="ok", model="t")
            def default_model(self):
                return "t"

        provider = RetryProvider(TrackingProvider(), max_retries=0)
        provider.complete("s", "u", model="custom", temperature=0.5, max_tokens=100)
        assert calls[0]["model"] == "custom"
        assert calls[0]["temperature"] == 0.5
        assert calls[0]["max_tokens"] == 100
