/**
 * Session reports — cross-session summary at run completion (AC-349 Task 39).
 * Mirrors Python's autocontext/knowledge/report.py.
 */

function toFloat(val: unknown, fallback = 0.0): number {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

export interface SessionReport {
  runId: string;
  scenario: string;
  startScore: number;
  endScore: number;
  startElo: number;
  endElo: number;
  totalGenerations: number;
  durationSeconds: number;
  scoringBackend: string;
  endRatingUncertainty: number | null;
  gateCounts: Record<string, number>;
  topImprovements: Array<Record<string, unknown>>;
  deadEndsFound: number;
  explorationMode: string;
  toMarkdown(): string;
}

export interface GenerateReportOpts {
  durationSeconds?: number;
  explorationMode?: string;
  deadEndsFound?: number;
}

export function generateSessionReport(
  runId: string,
  scenario: string,
  trajectoryRows: Array<Record<string, unknown>>,
  opts: GenerateReportOpts = {},
): SessionReport {
  const durationSeconds = opts.durationSeconds ?? 0;
  const explorationMode = opts.explorationMode ?? "linear";
  const deadEndsFound = opts.deadEndsFound ?? 0;

  if (trajectoryRows.length === 0) {
    return makeReport({
      runId,
      scenario,
      startScore: 0,
      endScore: 0,
      startElo: 1000,
      endElo: 1000,
      totalGenerations: 0,
      durationSeconds,
      scoringBackend: "elo",
      endRatingUncertainty: null,
      gateCounts: {},
      topImprovements: [],
      deadEndsFound,
      explorationMode,
    });
  }

  const first = trajectoryRows[0];
  const last = trajectoryRows[trajectoryRows.length - 1];

  // Count gate decisions
  const gateCounts: Record<string, number> = {};
  for (const row of trajectoryRows) {
    const decision = String(row.gate_decision ?? "unknown");
    gateCounts[decision] = (gateCounts[decision] ?? 0) + 1;
  }

  // Top improvements (positive deltas, sorted descending)
  const improvements: Array<Record<string, unknown>> = [];
  for (const row of trajectoryRows) {
    const delta = toFloat(row.delta, 0);
    if (delta > 0) {
      improvements.push({
        gen: row.generation_index ?? 0,
        delta,
        description: `Score improved to ${toFloat(row.best_score, 0).toFixed(4)}`,
      });
    }
  }
  improvements.sort((a, b) => toFloat(b.delta) - toFloat(a.delta));

  return makeReport({
    runId,
    scenario,
    startScore: toFloat(first.best_score),
    endScore: toFloat(last.best_score),
    startElo: toFloat(first.elo, 1000),
    endElo: toFloat(last.elo, 1000),
    totalGenerations: trajectoryRows.length,
    durationSeconds,
    scoringBackend: String(last.scoring_backend ?? first.scoring_backend ?? "elo"),
    endRatingUncertainty: last.rating_uncertainty != null ? toFloat(last.rating_uncertainty) : null,
    gateCounts,
    topImprovements: improvements.slice(0, 5),
    deadEndsFound,
    explorationMode,
  });
}

interface ReportData {
  runId: string;
  scenario: string;
  startScore: number;
  endScore: number;
  startElo: number;
  endElo: number;
  totalGenerations: number;
  durationSeconds: number;
  scoringBackend: string;
  endRatingUncertainty: number | null;
  gateCounts: Record<string, number>;
  topImprovements: Array<Record<string, unknown>>;
  deadEndsFound: number;
  explorationMode: string;
}

function makeReport(data: ReportData): SessionReport {
  return {
    ...data,
    toMarkdown(): string {
      const delta = data.endScore - data.startScore;
      const advances = data.gateCounts.advance ?? 0;
      const retries = data.gateCounts.retry ?? 0;
      const rollbacks = data.gateCounts.rollback ?? 0;
      const mins = Math.floor(data.durationSeconds / 60);
      const secs = Math.floor(data.durationSeconds % 60);
      const dur = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
      const ratingLabel = data.scoringBackend === "elo" ? "Elo" : `Rating (${data.scoringBackend})`;

      const lines = [
        `# Session Report: ${data.runId}`,
        `**Scenario:** ${data.scenario} | **Duration:** ${dur}`,
        "",
        "## Results",
        `- Score: ${data.startScore.toFixed(4)} → ${data.endScore.toFixed(4)} (Δ ${delta >= 0 ? "+" : ""}${delta.toFixed(4)})`,
        `- ${ratingLabel}: ${data.startElo.toFixed(1)} → ${data.endElo.toFixed(1)}`,
        `- Generations: ${data.totalGenerations} (${advances} advances, ${retries} retries, ${rollbacks} rollbacks)`,
        `- Exploration mode: ${data.explorationMode}`,
        "",
      ];

      lines.push("## Top Improvements");
      if (data.topImprovements.length > 0) {
        lines.push("| Gen | Delta | Description |");
        lines.push("|-----|-------|-------------|");
        for (const imp of data.topImprovements) {
          const d = toFloat(imp.delta);
          lines.push(`| ${imp.gen ?? "?"} | ${d >= 0 ? "+" : ""}${d.toFixed(4)} | ${imp.description ?? ""} |`);
        }
      } else {
        lines.push("No significant improvements recorded.");
      }
      lines.push("");
      lines.push("## Dead Ends Discovered");
      lines.push(`${data.deadEndsFound} dead ends identified.`);
      lines.push("");

      return lines.join("\n");
    },
  };
}
