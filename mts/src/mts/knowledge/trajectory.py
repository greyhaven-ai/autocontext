from __future__ import annotations

from typing import Any

from mts.storage.sqlite_store import SQLiteStore


def _detect_approach(content: str) -> str:
    """Classify strategy content as json, code, or text."""
    if content.startswith("{"):
        return "json"
    if "def " in content or "result =" in content:
        return "code"
    return "text"


class ScoreTrajectoryBuilder:
    def __init__(self, sqlite: SQLiteStore) -> None:
        self.sqlite = sqlite

    def build_trajectory(self, run_id: str) -> str:
        """Markdown table: Gen | Mean | Best | Elo | Gate | Delta"""
        rows = self.sqlite.get_generation_trajectory(run_id)
        if not rows:
            return ""
        header = "| Gen | Mean | Best | Elo | Gate | Delta |"
        sep = "|-----|------|------|-----|------|-------|"
        lines = ["## Score Trajectory", "", header, sep]
        for row in rows:
            lines.append(
                f"| {row['generation_index']} "
                f"| {row['mean_score']:.4f} "
                f"| {row['best_score']:.4f} "
                f"| {row['elo']:.1f} "
                f"| {row['gate_decision']} "
                f"| {row['delta']:+.4f} |"
            )
        return "\n".join(lines)

    def build_strategy_registry(self, run_id: str) -> str:
        """Markdown table: Gen | Strategy (truncated) | Best Score | Gate"""
        rows = self.sqlite.get_strategy_score_history(run_id)
        if not rows:
            return ""
        header = "| Gen | Strategy | Best Score | Gate |"
        sep = "|-----|----------|------------|------|"
        lines = ["## Strategy-Score Registry", "", header, sep]
        for row in rows:
            strategy_text = row["content"]
            if len(strategy_text) > 200:
                strategy_text = strategy_text[:200] + "..."
            lines.append(
                f"| {row['generation_index']} "
                f"| `{strategy_text}` "
                f"| {row['best_score']:.4f} "
                f"| {row['gate_decision']} |"
            )
        return "\n".join(lines)

    def build_experiment_log(self, run_id: str) -> str:
        """Markdown table: Gen | Strategy Summary | Score | Delta | Gate | Approach"""
        trajectory_rows = self.sqlite.get_generation_trajectory(run_id)
        if not trajectory_rows:
            return ""
        strategy_rows = self.sqlite.get_strategy_score_history(run_id)

        # Index strategy content by generation
        strategy_by_gen: dict[int, dict[str, Any]] = {}
        for row in strategy_rows:
            strategy_by_gen[row["generation_index"]] = row

        header = "| Gen | Strategy Summary | Score | Delta | Gate | Approach |"
        sep = "|-----|------------------|-------|-------|------|----------|"
        lines = ["## Experiment Log", "", header, sep]
        for trow in trajectory_rows:
            gen = trow["generation_index"]
            srow = strategy_by_gen.get(gen)
            if srow:
                content = srow["content"]
                if len(content) > 80:
                    content = content[:80] + "..."
                approach = _detect_approach(srow["content"])
            else:
                content = "(no strategy)"
                approach = "text"
            lines.append(
                f"| {gen} "
                f"| `{content}` "
                f"| {trow['best_score']:.4f} "
                f"| {trow['delta']:+.4f} "
                f"| {trow['gate_decision']} "
                f"| {approach} |"
            )
        return "\n".join(lines)
