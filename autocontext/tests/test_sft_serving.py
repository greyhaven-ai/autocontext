"""Serving path for the torch/peft SFT LoRA backend (AC-891 phase A, CI-safe, no torch).

Plan 5b added a TRL SFTTrainer backend emitting a torch/peft LoRA adapter, but the runtime
serving resolver only recognized mlx adapters, so an active ``sft`` record fell back to the
frontier client. These tests pin the wiring that makes the resolver recognize + build a torch
client for sft records, exercised without ever importing torch: the pure routing decision, the
resolver search order, the ``build_planned_client`` sft branch (with a fake client), and the
torch-absent construction guard the resolver caller relies on to fall back safely.
"""

from __future__ import annotations

import importlib.util

import pytest

from autocontext.agents.scenario_bound_clients import (
    LocalClientPlan,
    _resolve_local_record,
    build_planned_client,
)
from autocontext.config.settings import AppSettings

_HAS_TORCH = importlib.util.find_spec("torch") is not None


# --- _resolve_local_record: sft is in the backend search order ------------------------------


def test_resolve_local_record_search_order_includes_sft(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The resolver must query the ``sft`` backend (else an active sft record is never found)."""
    import autocontext.training.model_registry as mr

    queried: list[str] = []

    def fake_resolve(registry, *, scenario, backend, runtime_type):  # type: ignore[no-untyped-def]
        queried.append(backend)
        return None

    monkeypatch.setattr(mr, "resolve_model", fake_resolve)
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)

    assert _resolve_local_record(settings, "grid_ctf") is None
    assert queried == ["opd", "mlxlm", "grpo", "sft", "mlx"]


# --- build_planned_client: the sft branch builds the torch client --------------------------


def test_build_planned_client_sft_constructs_torch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A kind=="sft" plan constructs SftTorchClient with the base model + adapter + mlx temp/tokens.

    Patches the client symbol where the branch looks it up, so no real torch is imported in CI.
    """
    import autocontext.agents.sft_torch_client as sc

    captured: dict[str, object] = {}

    class _FakeSftTorchClient:
        def __init__(
            self, model: str, *, adapter_path: str | None = None, temperature: float = 0.8, max_tokens: int = 512
        ) -> None:
            captured.update(model=model, adapter_path=adapter_path, temperature=temperature, max_tokens=max_tokens)

    monkeypatch.setattr(sc, "SftTorchClient", _FakeSftTorchClient)

    plan = LocalClientPlan(kind="sft", model="Qwen/Qwen2.5-1.5B", adapter_path="/adapters/sft", score_conditioned=False)
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", mlx_temperature=0.7, mlx_max_tokens=256)

    client = build_planned_client(plan, settings)
    assert isinstance(client, _FakeSftTorchClient)
    assert captured == {
        "model": "Qwen/Qwen2.5-1.5B",
        "adapter_path": "/adapters/sft",
        "temperature": 0.7,
        "max_tokens": 256,
    }


# --- torch-absent construction guard: raises ImportError so the caller falls back ------------


@pytest.mark.skipif(_HAS_TORCH, reason="torch is installed; the absent-guard is only meaningful without torch")
def test_sft_torch_provider_requires_torch() -> None:
    """Without torch, constructing the provider raises ImportError (the resolver caller catches it)."""
    from autocontext.providers.sft_torch_provider import SftTorchProvider

    with pytest.raises((ImportError, ModuleNotFoundError)):
        SftTorchProvider("Qwen/Qwen2.5-1.5B")


@pytest.mark.skipif(_HAS_TORCH, reason="torch is installed; the absent-guard is only meaningful without torch")
def test_sft_torch_client_requires_torch() -> None:
    """The client construction propagates the provider's ImportError (built inside __init__)."""
    from autocontext.agents.sft_torch_client import SftTorchClient

    with pytest.raises((ImportError, ModuleNotFoundError)):
        SftTorchClient("Qwen/Qwen2.5-1.5B", adapter_path="/adapters/sft")


# --- real-generation smoke (skipped in CI: needs torch + transformers + a base model) -------


@pytest.mark.skipif(not _HAS_TORCH, reason="needs torch + transformers + a downloadable base model")
def test_sft_torch_provider_generates_end_to_end() -> None:  # pragma: no cover - env-gated
    from autocontext.providers.sft_torch_provider import SftTorchProvider

    provider = SftTorchProvider("hf-internal-testing/tiny-random-LlamaForCausalLM", max_tokens=8)
    result = provider.complete("", "Hello", temperature=0.0, max_tokens=8)
    assert isinstance(result.text, str)
    assert result.model == "hf-internal-testing/tiny-random-LlamaForCausalLM"
    assert result.usage["input_tokens"] >= 1
    assert provider.default_model() == "hf-internal-testing/tiny-random-LlamaForCausalLM"
