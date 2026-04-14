from __future__ import annotations


def is_action_plan_interface(strategy_interface: str) -> bool:
    """Return True when the strategy interface expects structured action plans.

    Simulation-style families describe strategies as ordered plans with an
    `actions` array, nested parameters, and allowed action names. Game-style
    scenarios instead expose flat numeric parameter dictionaries.
    """
    lowered = strategy_interface.lower()
    return (
        '"actions"' in strategy_interface
        or "`actions`" in strategy_interface
        or "ordered action plan" in lowered
        or "allowed action names" in lowered
    )
