"""Tests for librarian/archivist prefix-based routing in RoleRouter."""
from __future__ import annotations

from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass, RoleRouter
from autocontext.config.settings import AppSettings


# ---------------------------------------------------------------------------
# Routing table entries
# ---------------------------------------------------------------------------


def test_librarian_in_routing_table() -> None:
    assert "librarian" in DEFAULT_ROUTING_TABLE
    assert ProviderClass.MID_TIER in DEFAULT_ROUTING_TABLE["librarian"]


def test_archivist_in_routing_table() -> None:
    assert "archivist" in DEFAULT_ROUTING_TABLE
    assert ProviderClass.FRONTIER in DEFAULT_ROUTING_TABLE["archivist"]


# ---------------------------------------------------------------------------
# Prefix-based model resolution
# ---------------------------------------------------------------------------


def test_router_resolves_librarian_prefix() -> None:
    settings = AppSettings(model_librarian="claude-haiku-3-5-20241022")
    router = RoleRouter(settings)
    model = router.resolve_model("librarian_clean_arch")
    assert model == "claude-haiku-3-5-20241022"


def test_router_resolves_archivist() -> None:
    settings = AppSettings(model_archivist="claude-opus-4-6")
    router = RoleRouter(settings)
    model = router.resolve_model("archivist")
    assert model == "claude-opus-4-6"


def test_router_librarian_provider_override() -> None:
    settings = AppSettings(librarian_provider="openai")
    router = RoleRouter(settings)
    provider = router.resolve_provider("librarian_ddd")
    assert provider == "openai"
