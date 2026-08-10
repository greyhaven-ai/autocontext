"""AC-912: per-provider default model resolution.

Characterization first. The ``_BEFORE`` table was captured by running the real
``RoleRouter`` against real ``load_settings()`` output at 29199664, before any
behavior changed; ``_AFTER`` is the same measurement once the fix landed.
Keeping both makes the change a reviewable diff rather than an unverifiable
claim that nothing else moved.

The defect: with no ``AUTOCONTEXT_MODEL_*`` overrides set, every provider
except ``mlx`` resolved every role to a Claude model id, so pointing the loop
at a local server sent it ``claude-opus-4-6`` and the request failed at the
endpoint rather than at configuration time.

Two rows must never change, and are asserted against ``_BEFORE`` on purpose:

* ``anthropic`` is the shipped default, and AC-912 promises its behavior is
  byte-identical.
* ``mlx`` already resolved correctly, because its model comes from
  ``mlx_model_path`` rather than the shared role/tier defaults.
"""

from __future__ import annotations

import pytest

ROLES = ("competitor", "analyst", "coach", "architect", "curator", "translator")

_CLAUDE_SONNET = "claude-sonnet-4-5-20250929"
_CLAUDE_OPUS = "claude-opus-4-6"
_MLX_PATH = "/models/pinned-local"

_CLAUDE_BY_ROLE: dict[str, str] = {
    "competitor": _CLAUDE_SONNET,
    "analyst": _CLAUDE_SONNET,
    "coach": _CLAUDE_OPUS,
    "architect": _CLAUDE_OPUS,
    "curator": _CLAUDE_OPUS,
    "translator": _CLAUDE_SONNET,
}

# provider -> role -> resolved model, as of 29199664 (pre-fix).
_BEFORE: dict[str, dict[str, str]] = {provider: dict(_CLAUDE_BY_ROLE) for provider in ("anthropic", "ollama", "vllm", "openai")}
_BEFORE["mlx"] = dict.fromkeys(ROLES, _MLX_PATH)

# The same measurement after AC-912. Unchanged rows are spelled out rather
# than referenced so a future edit to one table cannot silently move the other.
_AFTER: dict[str, dict[str, str]] = {
    "anthropic": dict(_CLAUDE_BY_ROLE),
    "mlx": dict.fromkeys(ROLES, _MLX_PATH),
    "ollama": dict.fromkeys(ROLES, "llama3.1"),
    "vllm": dict.fromkeys(ROLES, "default"),
    "openai": dict.fromkeys(ROLES, "gpt-4o"),
}

_UNCHANGED_PROVIDERS = ("anthropic", "mlx")


def _settings(monkeypatch: pytest.MonkeyPatch, provider: str, **env: str):
    """Load real settings with a scrubbed AUTOCONTEXT_* environment."""
    import os

    from autocontext.config.settings import load_settings

    for key in list(os.environ):
        if key.startswith("AUTOCONTEXT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", provider)
    if provider == "mlx":
        monkeypatch.setenv("AUTOCONTEXT_MLX_MODEL_PATH", _MLX_PATH)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def _route(settings, role: str) -> str | None:
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    return RoleRouter(settings).route(role, context=RoutingContext()).model


@pytest.mark.parametrize("provider", sorted(_AFTER))
@pytest.mark.parametrize("role", ROLES)
def test_default_model_per_provider_and_role(monkeypatch: pytest.MonkeyPatch, provider: str, role: str) -> None:
    """Pin what each provider sends for each role when nothing is overridden."""
    assert _route(_settings(monkeypatch, provider), role) == _AFTER[provider][role]


@pytest.mark.parametrize("provider", _UNCHANGED_PROVIDERS)
@pytest.mark.parametrize("role", ROLES)
def test_preserved_providers_are_byte_identical_to_before(monkeypatch: pytest.MonkeyPatch, provider: str, role: str) -> None:
    """AC-912's hard constraint, asserted against the pre-fix measurement.

    Deliberately redundant with the table above: this one compares against
    ``_BEFORE``, so editing ``_AFTER`` to match a regression cannot make both
    pass.
    """
    assert _route(_settings(monkeypatch, provider), role) == _BEFORE[provider][role]


@pytest.mark.parametrize("provider", ("ollama", "vllm", "openai"))
def test_no_claude_id_reaches_a_non_anthropic_provider(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    """The inverse of the original defect test, which is the point of the fix.

    Its predecessor asserted that these providers DID receive Claude ids; that
    test failing is what proved the fix landed. It was replaced rather than
    deleted so the guarantee survives as an assertion.
    """
    settings = _settings(monkeypatch, provider)
    for role in ROLES:
        model = _route(settings, role)
        assert model is not None
        assert "claude" not in model.lower(), f"{provider}/{role} still receives {model}"


def test_local_model_fills_every_unset_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """One env var instead of eight-plus, which was the usability half of AC-912."""
    settings = _settings(monkeypatch, "ollama", AUTOCONTEXT_LOCAL_MODEL="qwen3:32b")
    assert {_route(settings, role) for role in ROLES} == {"qwen3:32b"}


def test_local_model_does_not_touch_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic keeps its per-role tiering even when a local model is named.

    Setting both is contradictory; resolving it in favor of the explicit
    provider keeps the byte-identical guarantee unconditional rather than
    "unless you also set this other var".
    """
    settings = _settings(monkeypatch, "anthropic", AUTOCONTEXT_LOCAL_MODEL="qwen3:32b")
    for role in ROLES:
        assert _route(settings, role) == _BEFORE["anthropic"][role]


def test_explicit_override_beats_the_provider_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly configured model wins, even against a local provider.

    The value chosen here is byte-equal to the Claude field default, which is
    the case a naive "is it still the default?" check gets wrong: the user did
    choose it, so it must survive.
    """
    settings = _settings(
        monkeypatch,
        "ollama",
        AUTOCONTEXT_MODEL_COMPETITOR=_CLAUDE_SONNET,
        AUTOCONTEXT_LOCAL_MODEL="qwen3:32b",
    )
    assert _route(settings, "competitor") == _CLAUDE_SONNET
    assert _route(settings, "analyst") == "qwen3:32b"


def test_unknown_provider_keeps_todays_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """An uncharacterized transport falls through unchanged.

    This module narrows a known leak; it does not invent defaults for
    providers nobody has measured.
    """
    settings = _settings(monkeypatch, "some-unknown-provider")
    assert _route(settings, "coach") == _CLAUDE_OPUS


def test_explicit_override_is_distinguishable_from_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mechanism the fix depends on.

    ``load_settings`` only passes kwargs for fields that came from the
    environment or a preset, so ``model_fields_set`` separates "the user chose
    this" from "nobody touched it" -- even when the chosen value is byte-equal
    to the default. Without that, the resolver cannot tell an unset field from
    a deliberate Claude choice, and would silently override the latter.
    """
    untouched = _settings(monkeypatch, "ollama")
    assert "model_competitor" not in untouched.model_fields_set

    chosen = _settings(monkeypatch, "ollama", AUTOCONTEXT_MODEL_COMPETITOR=_CLAUDE_SONNET)
    assert "model_competitor" in chosen.model_fields_set
    assert chosen.model_competitor == _CLAUDE_SONNET
