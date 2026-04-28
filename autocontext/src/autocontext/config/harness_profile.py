"""Runtime harness profile value objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings

_LEAN_PROFILE = "lean"
_STANDARD_PROFILE = "standard"


class HarnessRuntimeProfile(BaseModel):
    """Resolved runtime constraints for a harness execution surface."""

    name: str
    context_budget_tokens: int
    hidden_context_budget_tokens: int = 0
    tool_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    context_files_enabled: bool = True

    model_config = {"frozen": True}


def _parse_tool_allowlist(raw: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tools: list[str] = []
    for item in raw.split(","):
        tool = item.strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        tools.append(tool)
    return tuple(tools)


def _profile_value(raw: object) -> str:
    value = getattr(raw, "value", raw)
    return str(value)


def resolve_harness_runtime_profile(settings: AppSettings) -> HarnessRuntimeProfile:
    """Resolve high-level settings into concrete harness runtime constraints."""
    if _profile_value(settings.harness_profile) == _LEAN_PROFILE:
        budget = settings.lean_context_budget_tokens
        if settings.context_budget_tokens > 0:
            budget = min(settings.context_budget_tokens, budget)
        return HarnessRuntimeProfile(
            name=_LEAN_PROFILE,
            context_budget_tokens=budget,
            hidden_context_budget_tokens=settings.lean_hidden_context_budget_tokens,
            tool_allowlist=_parse_tool_allowlist(settings.lean_tool_allowlist),
            context_files_enabled=not settings.pi_no_context_files,
        )

    return HarnessRuntimeProfile(
        name=_STANDARD_PROFILE,
        context_budget_tokens=settings.context_budget_tokens,
        hidden_context_budget_tokens=settings.context_budget_tokens,
        tool_allowlist=(),
        context_files_enabled=True,
    )


def render_harness_tool_context(profile: HarnessRuntimeProfile, generated_tool_context: str) -> str:
    """Render the tool context allowed by a runtime harness profile."""
    if profile.name != _LEAN_PROFILE:
        return generated_tool_context

    lines = ["Lean harness tool allowlist:"]
    if profile.tool_allowlist:
        lines.extend(f"- {tool}" for tool in profile.tool_allowlist)
    else:
        lines.append("- none")
    return "\n".join(lines)
