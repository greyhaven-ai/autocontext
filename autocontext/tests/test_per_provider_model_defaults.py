"""AC-912: per-provider default model resolution.

Characterization first. Every value in ``_BASELINE`` below was captured by
running the real ``RoleRouter`` against real ``load_settings()`` output on
2026-08-09 at 29199664, before any behavior changed. The point of pinning a
defect is that the fix produces a *reviewable diff* of this table rather than
an unverifiable claim that nothing else moved.

The defect: with no ``AUTOCONTEXT_MODEL_*`` overrides set, every provider
except ``mlx`` resolves every role to a Claude model id, so pointing the loop
at a local server sends it ``claude-opus-4-6`` and the request fails at the
endpoint rather than at configuration time.

``mlx`` is the only provider that already resolves correctly today, because
it is the only one whose model comes from its own setting
(``mlx_model_path``) rather than from the shared role/tier defaults.
"""

from __future__ import annotations

import pytest

ROLES = ("competitor", "analyst", "coach", "architect", "curator", "translator")

_CLAUDE_SONNET = "claude-sonnet-4-5-20250929"
_CLAUDE_OPUS = "claude-opus-4-6"

# provider -> role -> resolved model, as of 29199664.
_BASELINE: dict[str, dict[str, str]] = {
    provider: {
        "competitor": _CLAUDE_SONNET,
        "analyst": _CLAUDE_SONNET,
        "coach": _CLAUDE_OPUS,
        "architect": _CLAUDE_OPUS,
        "curator": _CLAUDE_OPUS,
        "translator": _CLAUDE_SONNET,
    }
    for provider in ("anthropic", "ollama", "vllm", "openai")
}
_BASELINE["mlx"] = dict.fromkeys(ROLES, "/models/pinned-local")


def _settings(monkeypatch: pytest.MonkeyPatch, provider: str, **env: str):
    """Load real settings with a scrubbed AUTOCONTEXT_* environment."""
    import os

    from autocontext.config.settings import load_settings

    for key in list(os.environ):
        if key.startswith("AUTOCONTEXT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", provider)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def _route(settings, role: str) -> str | None:
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    return RoleRouter(settings).route(role, context=RoutingContext()).model


@pytest.mark.parametrize("provider", sorted(_BASELINE))
@pytest.mark.parametrize("role", ROLES)
def test_default_model_per_provider_and_role(monkeypatch: pytest.MonkeyPatch, provider: str, role: str) -> None:
    """Pin what each provider sends for each role when nothing is overridden."""
    env = {"AUTOCONTEXT_MLX_MODEL_PATH": "/models/pinned-local"} if provider == "mlx" else {}
    settings = _settings(monkeypatch, provider, **env)
    assert _route(settings, role) == _BASELINE[provider][role]


def test_non_anthropic_providers_currently_receive_claude_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect itself, stated as one assertion.

    This test is expected to FAIL once AC-912 lands -- that failure is the
    signal the fix worked, and this test must then be replaced by its inverse
    rather than deleted, so the guarantee survives.
    """
    for provider in ("ollama", "vllm", "openai"):
        settings = _settings(monkeypatch, provider)
        assert _route(settings, "coach") == _CLAUDE_OPUS, provider


def test_explicit_override_is_distinguishable_from_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mechanism AC-912's fix depends on.

    ``load_settings`` only passes kwargs for fields that came from the
    environment or a preset, so ``model_fields_set`` separates "the user chose
    this" from "nobody touched it" -- even when the chosen value is byte-equal
    to the default. Without that, a fix cannot tell an unset field from a
    deliberate Claude choice, and would silently override the latter.
    """
    untouched = _settings(monkeypatch, "ollama")
    assert "model_competitor" not in untouched.model_fields_set

    chosen = _settings(monkeypatch, "ollama", AUTOCONTEXT_MODEL_COMPETITOR=_CLAUDE_SONNET)
    assert "model_competitor" in chosen.model_fields_set
    assert chosen.model_competitor == _CLAUDE_SONNET
