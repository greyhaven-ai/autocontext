"""AC-935: providers that serve tiers get a model per tier, not one for all slots.

`PROVIDER_DEFAULT_MODEL` supplies one id per provider, and the comment in
`provider_model_defaults` explains why: a local endpoint typically serves one
model, so asking it for three tiers is meaningless.

That reasoning is right for ollama, vllm and mlx and wrong for OpenAI and
OpenRouter, which serve real tiers at real price differences. The visible
symptom was an OpenAI user getting the same model for the architect and the
curator -- defeating the point of routing by role, and paying flagship rates for
a classification call or fast-tier quality for the hardest reasoning in the loop.

The per-tier table lives in the shared contract so both languages read one
source; `ts/tests/role-routing-contract.test.ts` replays the same expectations.
"""

from __future__ import annotations

import pytest

from autocontext.agents.role_router import RoleRouter
from autocontext.config.settings import load_settings


def _router(monkeypatch: pytest.MonkeyPatch, provider: str) -> RoleRouter:
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", provider)
    monkeypatch.setenv("AUTOCONTEXT_ROLE_ROUTING", "auto")
    return RoleRouter(load_settings())


@pytest.mark.parametrize(
    "role,provider_class,model",
    [
        ("architect", "frontier", "gpt-5.6-sol"),
        ("competitor", "frontier", "gpt-5.6-sol"),
        ("analyst", "mid_tier", "gpt-5.6-terra"),
        ("coach", "mid_tier", "gpt-5.6-terra"),
        ("curator", "fast", "gpt-5.6-luna"),
        ("translator", "fast", "gpt-5.6-luna"),
    ],
)
def test_openai_roles_resolve_to_their_tier(monkeypatch: pytest.MonkeyPatch, role: str, provider_class: str, model: str) -> None:
    routed = _router(monkeypatch, "openai").route(role)
    assert (routed.provider_class.value, routed.model) == (provider_class, model)


def test_the_architect_and_the_curator_no_longer_share_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-935's stated acceptance criterion, asserted directly.

    Kept separate from the table above because it is the point rather than a
    case: before this, both resolved to the single provider default.
    """
    router = _router(monkeypatch, "openai")
    assert router.route("architect").model != router.route("curator").model


def test_openrouter_tiers_keep_their_vendor_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter ids are prefixed; a table that dropped the prefix would 404."""
    router = _router(monkeypatch, "openrouter")
    assert router.route("architect").model == "anthropic/claude-opus-5"
    assert router.route("curator").model == "anthropic/claude-haiku-4.5"


@pytest.mark.parametrize("provider", ["ollama", "vllm"])
def test_single_model_providers_keep_one_id_for_every_tier(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    """Absence from the table is a decision, not an omission.

    These endpoints serve one model, so asking them for three tiers would send
    two ids that are not loaded.
    """
    router = _router(monkeypatch, provider)
    models = {router.route(role).model for role in ("architect", "analyst", "curator")}
    assert len(models) == 1


def test_anthropic_defaults_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic is the preserved provider; its per-role tiering already worked."""
    router = _router(monkeypatch, "anthropic")
    assert router.route("architect").model == "claude-opus-5"
    assert router.route("analyst").model == "claude-sonnet-5"
    assert router.route("curator").model == "claude-haiku-4-5-20251001"


def test_an_explicit_tier_model_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-tier defaults fill unset slots; they never override a choice."""
    monkeypatch.setenv("AUTOCONTEXT_TIER_OPUS_MODEL", "my-own-frontier-model")
    router = _router(monkeypatch, "openai")
    assert router.route("architect").model == "my-own-frontier-model"
    # ...and the slots the user did NOT set still get their tier default.
    assert router.route("curator").model == "gpt-5.6-luna"


def test_local_model_still_overrides_every_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTOCONTEXT_LOCAL_MODEL is the one-var escape hatch and outranks tiers.

    Ordering matters: it is checked before the per-tier table, so pointing at a
    single local endpoint does not start requesting three ids it cannot serve.
    """
    monkeypatch.setenv("AUTOCONTEXT_LOCAL_MODEL", "my-local-model")
    router = _router(monkeypatch, "openai")
    models = {router.route(role).model for role in ("architect", "analyst", "curator")}
    assert models == {"my-local-model"}
