from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.providers.runtime_bridge import RuntimeBridgeProvider


def test_runtime_bridge_provider_closes_underlying_runtime() -> None:
    runtime = MagicMock()
    provider = RuntimeBridgeProvider(runtime, default_model_name="runtime-model")

    provider.close()

    runtime.close.assert_called_once_with()


def test_runtime_bridge_provider_exposes_runtime_concurrency_capability() -> None:
    runtime = MagicMock()
    runtime.supports_concurrent_requests = False
    provider = RuntimeBridgeProvider(runtime, default_model_name="runtime-model")

    assert provider.supports_concurrent_requests is False
