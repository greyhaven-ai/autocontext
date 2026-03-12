"""Render the autoresearch program.md template with scenario-specific variables."""
from __future__ import annotations

from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent / "program.md"


def render_program(
    *,
    scenario: str,
    strategy_schema: str,
    playbook_summary: str,
    dead_ends_summary: str,
    time_budget: str,
    memory_limit: str,
) -> str:
    """Render the program.md template with the given variables.

    Parameters
    ----------
    scenario:
        Name of the AutoContext scenario (e.g. ``grid_ctf``).
    strategy_schema:
        JSON schema or description of the strategy interface.
    playbook_summary:
        Current playbook knowledge summary.
    dead_ends_summary:
        Known dead-end strategies to avoid.
    time_budget:
        Training time budget in seconds.
    memory_limit:
        Peak memory limit in MB.

    Returns
    -------
    str
        The fully rendered program instructions.
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        scenario=scenario,
        strategy_schema=strategy_schema,
        playbook_summary=playbook_summary,
        dead_ends_summary=dead_ends_summary,
        time_budget=time_budget,
        memory_limit=memory_limit,
    )
