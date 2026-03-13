from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _to_float(val: Any, default: float = 0.0) -> float:  # noqa: ANN401
    """Safely convert a value to float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class SessionReport:
    run_id: str
    scenario: str
    start_score: float
    end_score: float
    start_elo: float
    end_elo: float
    total_generations: int
    duration_seconds: float
    gate_counts: dict[str, int] = field(default_factory=dict)
    top_improvements: list[dict[str, object]] = field(default_factory=list)
    dead_ends_found: int = 0
    exploration_mode: str = "linear"
    stale_lessons_count: int = 0
    superseded_lessons_count: int = 0

    def to_markdown(self) -> str:
        """Render report as markdown."""
        delta = self.end_score - self.start_score
        advances = self.gate_counts.get("advance", 0)
        retries = self.gate_counts.get("retry", 0)
        rollbacks = self.gate_counts.get("rollback", 0)

        mins = int(self.duration_seconds // 60)
        secs = int(self.duration_seconds % 60)
        duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        lines = [
            f"# Session Report: {self.run_id}",
            f"**Scenario:** {self.scenario} | **Duration:** {duration_str}",
            "",
            "## Results",
            f"- Score: {self.start_score:.4f} → {self.end_score:.4f} (Δ {delta:+.4f})",
            f"- Elo: {self.start_elo:.1f} → {self.end_elo:.1f}",
            f"- Generations: {self.total_generations} ({advances} advances, {retries} retries, {rollbacks} rollbacks)",
            f"- Exploration mode: {self.exploration_mode}",
            "",
        ]

        # Top improvements
        lines.append("## Top Improvements")
        if self.top_improvements:
            lines.append("| Gen | Delta | Description |")
            lines.append("|-----|-------|-------------|")
            for imp in self.top_improvements:
                lines.append(
                    f"| {imp.get('gen', '?')} "
                    f"| {_to_float(imp.get('delta', 0)):+.4f} "
                    f"| {imp.get('description', '')} |"
                )
        else:
            lines.append("No significant improvements recorded.")
        lines.append("")

        # Dead ends
        lines.append("## Dead Ends Discovered")
        lines.append(f"{self.dead_ends_found} dead ends identified.")
        lines.append("")

        # Lesson health (AC-236)
        if self.stale_lessons_count > 0 or self.superseded_lessons_count > 0:
            lines.append("## Lesson Health")
            lines.append(f"- Stale lessons: {self.stale_lessons_count}")
            lines.append(f"- Superseded lessons: {self.superseded_lessons_count}")
            lines.append("")

        return "\n".join(lines)


def generate_session_report(
    run_id: str,
    scenario: str,
    trajectory_rows: list[dict[str, object]],
    exploration_mode: str = "linear",
    duration_seconds: float = 0.0,
    dead_ends_found: int = 0,
    stale_lessons_count: int = 0,
    superseded_lessons_count: int = 0,
) -> SessionReport:
    """Generate a session report from trajectory data."""
    if not trajectory_rows:
        return SessionReport(
            run_id=run_id,
            scenario=scenario,
            start_score=0.0,
            end_score=0.0,
            start_elo=1000.0,
            end_elo=1000.0,
            total_generations=0,
            duration_seconds=duration_seconds,
            exploration_mode=exploration_mode,
            dead_ends_found=dead_ends_found,
            stale_lessons_count=stale_lessons_count,
            superseded_lessons_count=superseded_lessons_count,
        )

    first = trajectory_rows[0]
    last = trajectory_rows[-1]

    # Count gate decisions
    gate_counts: dict[str, int] = {}
    for row in trajectory_rows:
        decision = str(row.get("gate_decision", "unknown"))
        gate_counts[decision] = gate_counts.get(decision, 0) + 1

    # Find top improvements (positive deltas, sorted descending)
    improvements: list[dict[str, object]] = []
    for row in trajectory_rows:
        delta = _to_float(row.get("delta", 0))
        if delta > 0:
            improvements.append({
                "gen": row.get("generation_index", 0),
                "delta": delta,
                "description": f"Score improved to {_to_float(row.get('best_score', 0)):.4f}",
            })
    improvements.sort(key=lambda x: _to_float(x.get("delta", 0)), reverse=True)
    top_improvements = improvements[:5]  # Keep top 5

    return SessionReport(
        run_id=run_id,
        scenario=scenario,
        start_score=_to_float(first.get("best_score", 0)),
        end_score=_to_float(last.get("best_score", 0)),
        start_elo=_to_float(first.get("elo", 1000), 1000.0),
        end_elo=_to_float(last.get("elo", 1000), 1000.0),
        total_generations=len(trajectory_rows),
        duration_seconds=duration_seconds,
        gate_counts=gate_counts,
        top_improvements=top_improvements,
        dead_ends_found=dead_ends_found,
        exploration_mode=exploration_mode,
        stale_lessons_count=stale_lessons_count,
        superseded_lessons_count=superseded_lessons_count,
    )
