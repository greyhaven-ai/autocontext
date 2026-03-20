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
  dimension_summary?: Record<string, unknown>;
  scoring_backend: string;
  rating_uncertainty: number | null;
}

function formatDimensionTrajectory(history: Array<Record<string, number>>): string {
  if (history.length === 0) return "";

  const allDims = [...new Set(history.flatMap((entry) => Object.keys(entry)))].sort();
  if (allDims.length === 0) return "";

  const header = `Gen | ${allDims.map((d) => d.padStart(12, " ")).join(" | ")}`;
  const separator = "-".repeat(header.length);
  const lines = [header, separator];

  for (const [index, entry] of history.entries()) {
    const scores = allDims
      .map((dim) => (entry[dim] ?? 0).toFixed(4).padStart(12, " "))
      .join(" | ");
    lines.push(`${String(index + 1).padStart(3, " ")} | ${scores}`);
  }

  return lines.join("\n");
}

function extractBestDimensionHistory(rows: TrajectoryRow[]): Array<Record<string, number>> {
  const history: Array<Record<string, number>> = [];

  for (const row of rows) {
    const summary = row.dimension_summary;
    if (!summary || typeof summary !== "object" || Array.isArray(summary)) continue;

    const bestDimensions = (summary as Record<string, unknown>).best_dimensions;
    if (!bestDimensions || typeof bestDimensions !== "object" || Array.isArray(bestDimensions)) continue;

    const parsed: Record<string, number> = {};
    for (const [name, value] of Object.entries(bestDimensions)) {
      if (typeof value === "number" && Number.isFinite(value)) {
        parsed[name] = value;
      }
    }
    if (Object.keys(parsed).length > 0) {
      history.push(parsed);
    }
  }

  return history;
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

    const dimensionHistory = extractBestDimensionHistory(this.rows);
    const formattedDimensions = formatDimensionTrajectory(dimensionHistory);
    if (formattedDimensions) {
      lines.push("");
      lines.push("## Dimension Trajectory (Best Match)");
      lines.push("");
      lines.push("```text");
      lines.push(formattedDimensions);
      lines.push("```");
    }

    return lines.join("\n");
  }
}
