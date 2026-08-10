"""AC-912: resolve role and tier model defaults against the effective provider.

Every ``model_*`` and ``tier_*`` default in ``AppSettings`` is a Claude model
id. Those defaults are read unconditionally, so setting only
``AUTOCONTEXT_AGENT_PROVIDER=ollama`` sends ``claude-opus-4-6`` to a local
server, which fails at the endpoint instead of at configuration time. The user
has to discover and set eight-plus separate vars before anything works.

The resolution order here is deliberately conservative:

1. **An explicitly configured value always wins.** ``load_settings`` only
   passes kwargs for fields that came from the environment or a preset, so
   ``model_fields_set`` distinguishes "the user chose this" from "nobody
   touched it" -- even when the chosen value is byte-equal to the default.
   Without that distinction a fix cannot tell an unset field from a deliberate
   Claude choice, and would silently override the latter.
2. **``AUTOCONTEXT_LOCAL_MODEL`` fills every unset slot** for non-Anthropic
   providers, so the common case is one env var instead of eight.
3. **A known per-provider default**, below.
4. **Otherwise the field default stands**, unchanged.

``anthropic`` is deliberately absent from the table so it falls through to
step 4 and keeps today's per-role Claude ids byte for byte. Unknown providers
fall through for the same reason: this module narrows a leak, it does not
invent behavior for transports nobody has characterized.

The values mirror the per-provider fallbacks ``create_provider()`` already
applies in ``providers/registry.py`` when it is handed ``model=None``. Those
fallbacks exist but were unreachable from the role path, which always passes a
concrete value. A single default per provider (rather than per role) matches
how these servers actually work: a local endpoint typically serves one model,
so asking it for three tiers is meaningless.

``mlx`` is absent because it already resolves correctly -- its model comes from
``mlx_model_path``, its own setting, rather than the shared role/tier defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from autocontext.agents import role_routing_contract_generated as _contract

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Set as AbstractSet


class _SupportsFieldsSet(Protocol):
    """The slice of AppSettings this module needs.

    Narrower than AppSettings on purpose: it keeps the resolver testable with a
    stub and documents that nothing here depends on the rest of settings.
    """

    local_model: str

    @property
    def model_fields_set(self) -> AbstractSet[str]: ...


# Provider -> the model id to use when a role/tier slot was never configured.
# Keep in sync with providers/registry.py's create_provider() fallbacks.
#
# Sourced from docs/role-routing-contract.json (AC-911) rather than declared
# here. AC-912 shipped this table in Python only, and the TypeScript engine went
# on sending Claude ids to every self-hosted endpoint with nothing to catch it;
# moving it into the shared contract is what stops the two from drifting again.
PROVIDER_DEFAULT_MODEL: dict[str, str] = dict(_contract.PROVIDER_DEFAULT_MODEL)

# Providers whose defaults must not change. Anthropic is the shipped default
# and its per-role tiering is the behavior AC-912 promises to preserve exactly.
_PRESERVED_PROVIDERS = frozenset(_contract.MODEL_DEFAULT_PRESERVED_PROVIDERS)


def resolve_model_default(
    settings: _SupportsFieldsSet,
    *,
    provider: str,
    field_name: str,
    configured: str | None,
) -> str | None:
    """Return the model a role/tier slot should use for ``provider``.

    ``field_name`` is the ``AppSettings`` field backing the slot (for example
    ``"model_coach"`` or ``"tier_opus_model"``); ``configured`` is that field's
    current value. Returns ``configured`` unchanged whenever this module has no
    better-informed answer, so every caller can substitute it for a direct
    settings read without changing Anthropic behavior.
    """
    if field_name in settings.model_fields_set:
        return configured

    normalized = provider.strip().lower()
    if normalized in _PRESERVED_PROVIDERS:
        return configured

    local_model = (settings.local_model or "").strip()
    if local_model:
        return local_model

    return PROVIDER_DEFAULT_MODEL.get(normalized, configured)
