from __future__ import annotations

from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.providers.base import ProviderError


def runtime_timeout_field_for_provider(provider_name: str) -> str | None:
    provider = provider_name.strip().lower()
    if provider == "claude-cli":
        return "claude_timeout"
    if provider == "codex":
        return "codex_timeout"
    if provider in {"pi", "pi-rpc"}:
        return "pi_timeout"
    return None


def apply_judge_runtime_overrides(
    settings: AppSettings,
    *,
    provider_name: str = "",
    model: str = "",
    timeout: float | None = None,
) -> AppSettings:
    updates: dict[str, Any] = {}
    if provider_name:
        updates["judge_provider"] = provider_name
    if model:
        updates["judge_model"] = model

    resolved_provider = (provider_name or settings.judge_provider).strip().lower()
    timeout_field = runtime_timeout_field_for_provider(resolved_provider)
    if timeout is not None and timeout_field:
        updates[timeout_field] = timeout

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def format_runtime_provider_error(
    exc: ProviderError,
    *,
    provider_name: str,
    settings: AppSettings,
) -> str:
    message = str(exc)
    if "timeout" not in message.lower():
        return message

    provider = provider_name.strip().lower()
    timeout_help = {
        "claude-cli": ("Claude CLI", settings.claude_timeout, "AUTOCONTEXT_CLAUDE_TIMEOUT"),
        "codex": ("Codex CLI", settings.codex_timeout, "AUTOCONTEXT_CODEX_TIMEOUT"),
        "pi": ("Pi CLI", settings.pi_timeout, "AUTOCONTEXT_PI_TIMEOUT"),
        "pi-rpc": ("Pi RPC", settings.pi_timeout, "AUTOCONTEXT_PI_TIMEOUT"),
    }
    help_details = timeout_help.get(provider)
    if help_details is None:
        return message

    label, configured_timeout, env_var = help_details
    return (
        f"{label} timed out after {configured_timeout:.0f}s. "
        f"Retry with --timeout <seconds> or set {env_var}. Original error: {message}"
    )
