"""Tests for consult_library in Agent SDK tool permissions."""
from __future__ import annotations

from autocontext.agents.agent_sdk_client import ROLE_TOOL_CONFIG


def test_all_roles_have_consult_library() -> None:
    for role in ("competitor", "analyst", "coach", "architect"):
        assert "consult_library" in ROLE_TOOL_CONFIG.get(role, []), f"{role} missing consult_library"


def test_librarian_roles_no_consult_library() -> None:
    assert "consult_library" not in ROLE_TOOL_CONFIG.get("translator", [])
    assert "consult_library" not in ROLE_TOOL_CONFIG.get("curator", [])
