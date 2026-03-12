from __future__ import annotations

from autocontext.storage.sqlite_store import SQLiteStore


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

    def build_experiment_log(self, run_id: str) -> str:
        """Collect RLM trial summaries across generations into an experiment log."""
        rows = self.sqlite.get_agent_outputs_by_role(run_id, "competitor_rlm_trials")
        if not rows:
            return ""
        lines = ["## RLM Experiment Log", ""]
        for row in rows:
            lines.append(str(row["content"]))
            lines.append("")
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
