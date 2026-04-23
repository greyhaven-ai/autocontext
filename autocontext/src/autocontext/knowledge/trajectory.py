from __future__ import annotations

from autocontext.harness.evaluation.dimensional import format_dimension_trajectory
from autocontext.knowledge.compaction import compact_prompt_components
from autocontext.storage.sqlite_store import SQLiteStore


class ScoreTrajectoryBuilder:
    def __init__(self, sqlite: SQLiteStore) -> None:
        self.sqlite = sqlite

    def build_trajectory(self, run_id: str) -> str:
        """Markdown table: Gen | Mean | Best | Elo | Gate | Delta"""
        rows = self.sqlite.get_generation_trajectory(run_id)
        if not rows:
            return ""
        non_elo = any(str(row.get("scoring_backend", "elo")) != "elo" for row in rows)
        show_uncertainty = any(row.get("rating_uncertainty") is not None for row in rows)
        rating_label = "Rating" if non_elo else "Elo"
        if show_uncertainty:
            header = f"| Gen | Mean | Best | {rating_label} | Uncertainty | Gate | Delta |"
            sep = "|-----|------|------|--------|-------------|------|-------|"
        else:
            header = f"| Gen | Mean | Best | {rating_label} | Gate | Delta |"
            sep = "|-----|------|------|--------|------|-------|"
        lines = ["## Score Trajectory", ""]
        if non_elo:
            lines.append(f"Backend: `{rows[-1].get('scoring_backend', 'elo')}`")
            lines.append("")
        lines.extend([header, sep])
        for row in rows:
            if show_uncertainty:
                uncertainty = row.get("rating_uncertainty")
                uncertainty_text = f"{float(uncertainty):.2f}" if isinstance(uncertainty, (int, float)) else "-"
                lines.append(
                    f"| {row['generation_index']} "
                    f"| {row['mean_score']:.4f} "
                    f"| {row['best_score']:.4f} "
                    f"| {row['elo']:.1f} "
                    f"| {uncertainty_text} "
                    f"| {row['gate_decision']} "
                    f"| {row['delta']:+.4f} |"
                )
            else:
                lines.append(
                    f"| {row['generation_index']} "
                    f"| {row['mean_score']:.4f} "
                    f"| {row['best_score']:.4f} "
                    f"| {row['elo']:.1f} "
                    f"| {row['gate_decision']} "
                    f"| {row['delta']:+.4f} |"
                )
        dimension_history = [
            row["dimension_summary"].get("best_dimensions", {})
            for row in rows
            if isinstance(row.get("dimension_summary"), dict)
        ]
        dimension_history = [
            entry
            for entry in dimension_history
            if isinstance(entry, dict) and entry
        ]
        if dimension_history:
            formatted = format_dimension_trajectory(dimension_history)
            if formatted:
                lines.extend([
                    "",
                    "## Dimension Trajectory (Best Match)",
                    "",
                    "```text",
                    formatted,
                    "```",
                ])
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
        return compact_prompt_components({"experiment_log": "\n".join(lines)})["experiment_log"]

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
