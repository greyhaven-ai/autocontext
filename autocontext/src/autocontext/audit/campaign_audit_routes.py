"""Canonical backend identities for independent campaign-audit routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator

from autocontext.providers.registry import resolve_provider_base_url
from autocontext.util.models import StrictModel

_HTTP_PROVIDER_TYPES = frozenset({"openai", "openai-compatible", "openrouter", "ollama", "vllm"})
_EXPLICIT_ENDPOINT_PROVIDER_TYPES = _HTTP_PROVIDER_TYPES | {"hermes"}


class CampaignAuditRoute(StrictModel):
    """One model route plus its credential-free resolved backend identity."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    backend_identity: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _infer_default_backend_identity(cls, value: Any) -> Any:
        # Backward-compatible durable records did not carry endpoint identity.
        # Their provider default is the only destination that can be proven.
        if isinstance(value, Mapping) and not value.get("backend_identity"):
            migrated = dict(value)
            provider = migrated.get("provider")
            if isinstance(provider, str) and provider.strip():
                migrated["backend_identity"] = route_backend_identity(provider)
            return migrated
        return value

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("campaign audit route provider must be non-empty")
        return normalized

    @field_validator("model")
    @classmethod
    def _normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("campaign audit route model must be non-empty")
        return normalized

    @field_validator("backend_identity")
    @classmethod
    def _normalize_backend_identity(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("endpoint:"):
            return f"endpoint:{canonical_backend_endpoint(normalized.removeprefix('endpoint:'))}"
        if normalized.startswith("client:"):
            client = normalized.removeprefix("client:").strip().lower()
            if not client or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for character in client):
                raise ValueError("campaign audit client identity is invalid")
            return f"client:{client}"
        raise ValueError("campaign audit backend identity must identify an endpoint or client")

    @model_validator(mode="after")
    def _backend_kind_matches_provider(self) -> Self:
        if self.provider in _HTTP_PROVIDER_TYPES and not self.backend_identity.startswith("endpoint:"):
            raise ValueError("OpenAI-compatible campaign audit routes must identify their resolved endpoint")
        if self.provider not in _EXPLICIT_ENDPOINT_PROVIDER_TYPES:
            expected = f"client:{self.provider}"
            if self.backend_identity != expected:
                raise ValueError("campaign audit client identity must match its provider")
        return self

    @classmethod
    def resolved(
        cls,
        provider: str,
        model: str,
        *,
        base_url: str | None = None,
    ) -> CampaignAuditRoute:
        """Build the identity from the endpoint the runtime will actually use."""

        return cls(
            provider=provider,
            model=model,
            backend_identity=route_backend_identity(provider, base_url=base_url),
        )

    @property
    def independence_identity(self) -> tuple[str, str]:
        return self.backend_identity, self.model


def canonical_backend_endpoint(value: str) -> str:
    """Return a stable, credential-free HTTP endpoint identity."""

    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("campaign audit backend endpoint is invalid") from exc
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("campaign audit backend endpoint must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("campaign audit backend endpoint must not contain credentials, query, or fragment")
    if port == (443 if scheme == "https" else 80):
        port = None
    rendered_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def route_backend_identity(provider: str, *, base_url: str | None = None) -> str:
    """Resolve the durable identity for one provider/client construction."""

    normalized = provider.strip().lower()
    if not normalized:
        raise ValueError("campaign audit route provider must be non-empty")
    if base_url is not None and base_url.strip() and normalized not in _EXPLICIT_ENDPOINT_PROVIDER_TYPES:
        raise ValueError(f"campaign audit provider {normalized!r} does not support a configured endpoint")
    configured = base_url.strip() if isinstance(base_url, str) and base_url.strip() else None
    if normalized in _HTTP_PROVIDER_TYPES:
        endpoint = resolve_provider_base_url(normalized, configured)
        if endpoint is None:
            raise ValueError(f"campaign audit provider {normalized!r} has no resolvable endpoint")
        return f"endpoint:{canonical_backend_endpoint(endpoint)}"
    if normalized == "hermes" and configured is not None:
        return f"endpoint:{canonical_backend_endpoint(configured)}"
    return f"client:{normalized}"


def normalize_proposer_routes(
    legacy_provider: str,
    legacy_model: str,
    routes: Sequence[CampaignAuditRoute],
) -> list[CampaignAuditRoute]:
    """Return a stable complete set, falling back to the legacy single route."""

    selected = routes or (CampaignAuditRoute.resolved(legacy_provider, legacy_model),)
    by_identity = {route.independence_identity: route for route in selected}
    return [by_identity[identity] for identity in sorted(by_identity)]


def auditor_route_is_distinct(
    auditor_route: CampaignAuditRoute,
    proposer_routes: Sequence[CampaignAuditRoute],
) -> bool:
    return all(route.independence_identity != auditor_route.independence_identity for route in proposer_routes)


__all__ = ["CampaignAuditRoute", "canonical_backend_endpoint", "route_backend_identity"]
