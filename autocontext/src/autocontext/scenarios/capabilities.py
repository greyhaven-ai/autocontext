"""Typed scenario capability adapters (AC-144).

Replaces ad-hoc ``hasattr()`` dispatch in MCP tools and knowledge/search
with explicit, typed capability resolution. Each adapter function
encapsulates one capability check and returns a typed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ScenarioCapabilities:
    """Resolved capability flags for a scenario instance."""

    describable: bool
    action_validating: bool
    match_runnable: bool
    task_bearing: bool
    rubric_bearing: bool
    is_game: bool
    is_agent_task: bool


def resolve_capabilities(scenario: Any) -> ScenarioCapabilities:
    """Resolve capabilities from a scenario instance.

    Uses callable checks (not just hasattr) to distinguish real methods
    from inherited stubs.
    """
    has_describe_rules = callable(getattr(scenario, "describe_rules", None))
    has_describe_task = callable(getattr(scenario, "describe_task", None))
    has_validate_actions = callable(getattr(scenario, "validate_actions", None))
    has_execute_match = callable(getattr(scenario, "execute_match", None))
    has_get_task_prompt = callable(getattr(scenario, "get_task_prompt", None))
    has_get_rubric = callable(getattr(scenario, "get_rubric", None))

    is_game = has_describe_rules and has_execute_match
    is_agent_task = has_describe_task and has_get_task_prompt and has_get_rubric

    return ScenarioCapabilities(
        describable=has_describe_rules or has_describe_task,
        action_validating=has_validate_actions and not is_agent_task,
        match_runnable=has_execute_match and not is_agent_task,
        task_bearing=has_get_task_prompt and has_get_rubric,
        rubric_bearing=has_get_rubric,
        is_game=is_game and not is_agent_task,
        is_agent_task=is_agent_task,
    )


def get_description(scenario: Any) -> str:
    """Get scenario description, dispatching to describe_rules or describe_task."""
    if callable(getattr(scenario, "describe_rules", None)):
        return str(scenario.describe_rules())
    if callable(getattr(scenario, "describe_task", None)):
        return str(scenario.describe_task())
    return ""


def get_evaluation_criteria(scenario: Any) -> str:
    """Get evaluation criteria, dispatching appropriately.

    Game scenarios use describe_evaluation_criteria().
    Agent tasks fall back to get_rubric().
    """
    if callable(getattr(scenario, "describe_evaluation_criteria", None)):
        return str(scenario.describe_evaluation_criteria())
    if callable(getattr(scenario, "get_rubric", None)):
        return str(scenario.get_rubric())
    return ""


def can_validate_actions(scenario: Any) -> bool:
    """Whether the scenario supports action validation."""
    caps = resolve_capabilities(scenario)
    return caps.action_validating


def can_run_match(scenario: Any) -> bool:
    """Whether the scenario supports direct match execution."""
    caps = resolve_capabilities(scenario)
    return caps.match_runnable


def get_task_prompt_safe(scenario: Any) -> str | None:
    """Get task prompt if available, None otherwise."""
    fn = getattr(scenario, "get_task_prompt", None)
    if not callable(fn):
        return None
    state_fn = getattr(scenario, "initial_state", None)
    state = state_fn() if callable(state_fn) else {}
    return str(fn(state))


def get_rubric_safe(scenario: Any) -> str | None:
    """Get rubric if available, None otherwise."""
    fn = getattr(scenario, "get_rubric", None)
    if not callable(fn):
        return None
    return str(fn())


def get_strategy_interface_safe(scenario: Any) -> str | None:
    """Get strategy interface if available, None otherwise."""
    fn = getattr(scenario, "describe_strategy_interface", None)
    if not callable(fn):
        return None
    return str(fn())
