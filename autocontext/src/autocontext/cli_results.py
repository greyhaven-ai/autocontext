"""Result models and Rich renderers shared by CLI execution paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass(slots=True)
class AgentTaskRunSummary:
    """Result summary for an agent-task execution via the CLI."""

    run_id: str
    scenario: str
    best_score: float
    best_output: str
    total_rounds: int
    met_threshold: bool
    termination_reason: str
    optimizer_metadata: dict[str, str] | None = None


def print_agent_task_run_summary(console: Console, summary: AgentTaskRunSummary) -> None:
    """Render an agent-task result as a compact table."""
    table = Table(title="Agent Task Result")
    table.add_column("Run ID")
    table.add_column("Scenario")
    table.add_column("Best Score")
    table.add_column("Rounds")
    table.add_column("Threshold Met")
    table.add_column("Termination")
    table.add_row(
        summary.run_id,
        summary.scenario,
        f"{summary.best_score:.4f}",
        str(summary.total_rounds),
        str(summary.met_threshold),
        summary.termination_reason,
    )
    console.print(table)


def print_generation_run_summary(console: Console, summary: Any) -> None:
    """Render a standard generation-loop result as a compact table."""
    table = Table(title="autocontext Run Summary")
    table.add_column("Run ID")
    table.add_column("Scenario")
    table.add_column("Generations")
    table.add_column("Best Score")
    table.add_column("Elo")
    table.add_row(
        summary.run_id,
        summary.scenario,
        str(summary.generations_executed),
        f"{summary.best_score:.4f}",
        f"{summary.current_elo:.2f}",
    )
    console.print(table)
