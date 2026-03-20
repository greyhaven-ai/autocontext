/**
 * Score trajectory builder — markdown table from generation data (AC-344 Task 11).
 * Mirrors Python's autocontext/knowledge/trajectory.py.
 */

export interface TrajectoryRow {
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  gate_decision: string;
  delta: number;
  scoring_backend: string;
  rating_uncertainty: number | null;
}

export class ScoreTrajectoryBuilder {
  private rows: TrajectoryRow[];

  constructor(rows: TrajectoryRow[]) {
    this.rows = rows;
  }

  build(): string {
    if (this.rows.length === 0) return "";

    const nonElo = this.rows.some((r) => r.scoring_backend !== "elo");
    const showUncertainty = this.rows.some((r) => r.rating_uncertainty != null);
    const ratingLabel = nonElo ? "Rating" : "Elo";

    const lines: string[] = ["## Score Trajectory", ""];

    if (nonElo) {
      lines.push(`Backend: \`${this.rows[this.rows.length - 1].scoring_backend}\``);
      lines.push("");
    }

    if (showUncertainty) {
      lines.push(`| Gen | Mean | Best | ${ratingLabel} | Uncertainty | Gate | Delta |`);
      lines.push("|-----|------|------|--------|-------------|------|-------|");
    } else {
      lines.push(`| Gen | Mean | Best | ${ratingLabel} | Gate | Delta |`);
      lines.push("|-----|------|------|--------|------|-------|");
    }

    for (const row of this.rows) {
      const delta = row.delta >= 0 ? `+${row.delta.toFixed(4)}` : row.delta.toFixed(4);
      if (showUncertainty) {
        const unc =
          row.rating_uncertainty != null ? row.rating_uncertainty.toFixed(2) : "-";
        lines.push(
          `| ${row.generation_index} ` +
            `| ${row.mean_score.toFixed(4)} ` +
            `| ${row.best_score.toFixed(4)} ` +
            `| ${row.elo.toFixed(1)} ` +
            `| ${unc} ` +
            `| ${row.gate_decision} ` +
            `| ${delta} |`,
        );
      } else {
        lines.push(
          `| ${row.generation_index} ` +
            `| ${row.mean_score.toFixed(4)} ` +
            `| ${row.best_score.toFixed(4)} ` +
            `| ${row.elo.toFixed(1)} ` +
            `| ${row.gate_decision} ` +
            `| ${delta} |`,
        );
      }
    }

    return lines.join("\n");
  }
}
