"""Tests for Pi-shaped harness profile resolution."""

from __future__ import annotations

import pytest


def test_standard_harness_profile_preserves_runtime_budget() -> None:
    from autocontext.config.harness_profile import resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings

    settings = AppSettings(context_budget_tokens=100_000)
    profile = resolve_harness_runtime_profile(settings)

    assert profile.name == "standard"
    assert profile.context_budget_tokens == 100_000
    assert profile.tool_allowlist == ()
    assert profile.context_files_enabled is True


def test_lean_harness_profile_caps_budget_and_uses_minimal_tools() -> None:
    from autocontext.config.harness_profile import resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings, HarnessProfile

    settings = AppSettings(harness_profile=HarnessProfile.LEAN, context_budget_tokens=100_000)
    profile = resolve_harness_runtime_profile(settings)

    assert profile.name == "lean"
    assert profile.context_budget_tokens == settings.lean_context_budget_tokens
    assert profile.tool_allowlist == ("read", "bash", "edit", "write")
    assert profile.hidden_context_budget_tokens == 0


def test_lean_harness_profile_respects_smaller_explicit_context_budget() -> None:
    from autocontext.config.harness_profile import resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings, HarnessProfile

    settings = AppSettings(
        harness_profile=HarnessProfile.LEAN,
        context_budget_tokens=8_000,
        lean_context_budget_tokens=32_000,
    )
    profile = resolve_harness_runtime_profile(settings)

    assert profile.context_budget_tokens == 8_000


def test_lean_harness_profile_parses_custom_tool_allowlist() -> None:
    from autocontext.config.harness_profile import resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings, HarnessProfile

    settings = AppSettings(
        harness_profile=HarnessProfile.LEAN,
        lean_tool_allowlist="read, grep, find, read, ",
    )
    profile = resolve_harness_runtime_profile(settings)

    assert profile.tool_allowlist == ("read", "grep", "find")


def test_standard_harness_profile_keeps_generated_tool_context() -> None:
    from autocontext.config.harness_profile import render_harness_tool_context, resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings

    profile = resolve_harness_runtime_profile(AppSettings())

    assert render_harness_tool_context(profile, "Generated tool source") == "Generated tool source"


def test_lean_harness_profile_replaces_generated_tool_context_with_allowlist() -> None:
    from autocontext.config.harness_profile import render_harness_tool_context, resolve_harness_runtime_profile
    from autocontext.config.settings import AppSettings, HarnessProfile

    profile = resolve_harness_runtime_profile(
        AppSettings(harness_profile=HarnessProfile.LEAN, lean_tool_allowlist="read,bash"),
    )

    rendered = render_harness_tool_context(profile, "Generated tool source")

    assert "Generated tool source" not in rendered
    assert "Lean harness tool allowlist" in rendered
    assert "- read" in rendered
    assert "- bash" in rendered


def test_load_settings_reads_harness_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.config.settings import HarnessProfile, load_settings

    monkeypatch.setenv("AUTOCONTEXT_HARNESS_PROFILE", "lean")
    monkeypatch.setenv("AUTOCONTEXT_LEAN_TOOL_ALLOWLIST", "read,bash")

    settings = load_settings()

    assert settings.harness_profile == HarnessProfile.LEAN
    assert settings.lean_tool_allowlist == "read,bash"
