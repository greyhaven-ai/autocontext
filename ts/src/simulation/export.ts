/**
 * Simulation export — portable result packages (AC-452).
 *
 * Exports saved simulation results as JSON, CSV, or Markdown reports.
 * Each format includes spec, variables, results, assumptions, and warnings.
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { loadPersistedSimulationSpec, resolveSimulationArtifact } from "./artifact-store.js";
import type { SimulationResult } from "./types.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ExportFormat = "json" | "markdown" | "csv";

export interface SimulationExportOpts {
  id: string;
  knowledgeRoot: string;
  format?: ExportFormat;
  outputDir?: string;
}

export interface SimulationExportResult {
  status: "completed" | "failed";
  format: ExportFormat;
  outputPath?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export function exportSimulation(opts: SimulationExportOpts): SimulationExportResult {
  const format = normalizeExportFormat(opts.format);
  if (!format) {
    return {
      status: "failed",
      format: "json",
      error: `Unsupported export format '${String(opts.format)}'. Use json, markdown, or csv.`,
    };
  }

  const resolved = resolveSimulationArtifact(opts.knowledgeRoot, opts.id);
  if (!resolved) {
    const simDir = join(opts.knowledgeRoot, "_simulations", opts.id);
    return { status: "failed", format, error: `Simulation '${opts.id}' not found at ${simDir}` };
  }

  const { scenarioDir, report } = resolved;

  // Load spec if available
  const specPath = join(scenarioDir, "spec.json");
  const spec = loadPersistedSimulationSpec(specPath) ?? {};

  const outputDir = opts.outputDir ?? join(scenarioDir, "exports");
  if (!existsSync(outputDir)) mkdirSync(outputDir, { recursive: true });

  switch (format) {
    case "json":
      return exportJSON(report, spec, outputDir);
    case "markdown":
      return exportMarkdown(report, spec, outputDir);
    case "csv":
      return exportCSV(report, outputDir);
  }
}

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

function exportJSON(
  report: SimulationResult,
  spec: Record<string, unknown>,
  outputDir: string,
): SimulationExportResult {
  const stem = exportFileStem(report);
  const pkg = {
    id: report.id,
    name: report.name,
    family: report.family,
    description: report.description,
    spec,
    variables: report.variables ?? {},
    results: {
      score: report.summary.score,
      reasoning: report.summary.reasoning,
      dimensionScores: report.summary.dimensionScores,
      bestCase: report.summary.bestCase,
      worstCase: report.summary.worstCase,
      mostSensitiveVariables: report.summary.mostSensitiveVariables,
    },
    sweep: report.sweep ?? null,
    execution: report.execution ?? null,
    assumptions: report.assumptions ?? [],
    warnings: report.warnings ?? [],
    replayOf: report.replayOf ?? null,
    originalScore: report.originalScore ?? null,
    scoreDelta: report.scoreDelta ?? null,
    exportedAt: new Date().toISOString(),
  };

  const outputPath = join(outputDir, `${stem}_export.json`);
  writeFileSync(outputPath, JSON.stringify(pkg, null, 2), "utf-8");
  return { status: "completed", format: "json", outputPath };
}

// ---------------------------------------------------------------------------
// Markdown
// ---------------------------------------------------------------------------

function exportMarkdown(
  report: SimulationResult,
  spec: Record<string, unknown>,
  outputDir: string,
): SimulationExportResult {
  const stem = exportFileStem(report);
  const lines: string[] = [];

  lines.push(`# Simulation Report: ${report.name}`);
  lines.push("");
  lines.push(`**Family:** ${report.family}`);
  lines.push(`**Status:** ${report.status}`);
  lines.push(`**Description:** ${report.description}`);
  if (report.replayOf) {
    lines.push(`**Replay Of:** ${report.replayOf}`);
  }
  lines.push("");

  // Score
  lines.push("## Score");
  lines.push("");
  lines.push(`**Overall:** ${report.summary.score.toFixed(4)}`);
  lines.push(`**Reasoning:** ${report.summary.reasoning}`);
  lines.push("");

  // Dimension scores
  const dims = report.summary.dimensionScores ?? {};
  if (Object.keys(dims).length > 0) {
    lines.push("### Dimension Scores");
    lines.push("");
    lines.push("| Dimension | Score |");
    lines.push("|-----------|-------|");
    for (const [dim, val] of Object.entries(dims)) {
      lines.push(`| ${dim} | ${(val as number).toFixed(4)} |`);
    }
    lines.push("");
  }

  // Best/worst case
  if (report.summary.bestCase) {
    lines.push(`**Best case:** ${report.summary.bestCase.score.toFixed(4)}`);
  }
  if (report.summary.worstCase) {
    lines.push(`**Worst case:** ${report.summary.worstCase.score.toFixed(4)}`);
  }
  if (report.summary.mostSensitiveVariables?.length) {
    lines.push(`**Most sensitive:** ${report.summary.mostSensitiveVariables.join(", ")}`);
  }
  lines.push("");

  // Variables
  const vars = report.variables ?? {};
  if (Object.keys(vars).length > 0) {
    lines.push("## Variables");
    lines.push("");
    lines.push("| Variable | Value |");
    lines.push("|----------|-------|");
    for (const [key, val] of Object.entries(vars)) {
      lines.push(`| ${key} | ${JSON.stringify(val)} |`);
    }
    lines.push("");
  }

  // Sweep
  if (report.sweep) {
    lines.push("## Sweep");
    lines.push("");
    lines.push(`**Dimensions:** ${report.sweep.dimensions.length}`);
    lines.push(`**Total runs:** ${report.sweep.runs}`);
    lines.push("");
    lines.push("| Variables | Score |");
    lines.push("|-----------|-------|");
    for (const run of report.sweep.results) {
      lines.push(`| ${JSON.stringify(run.variables)} | ${run.score.toFixed(4)} |`);
    }
    lines.push("");
  }

  // Assumptions
  if (report.assumptions?.length) {
    lines.push("## Assumptions");
    lines.push("");
    for (const a of report.assumptions) lines.push(`- ${a}`);
    lines.push("");
  }

  // Warnings
  if (report.warnings?.length) {
    lines.push("## Warnings");
    lines.push("");
    for (const w of report.warnings) lines.push(`- ⚠ ${w}`);
    lines.push("");
  }

  lines.push("---");
  lines.push(`*Exported at ${new Date().toISOString()}*`);

  const outputPath = join(outputDir, `${stem}_report.md`);
  writeFileSync(outputPath, lines.join("\n"), "utf-8");
  return { status: "completed", format: "markdown", outputPath };
}

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------

function exportCSV(report: SimulationResult, outputDir: string): SimulationExportResult {
  const stem = exportFileStem(report);
  const dims = collectDimensionKeys(report);
  const varKeys = collectVariableKeys(report);

  // Build header
  const headers = [...varKeys, "score", ...dims.map((d) => `dim_${d}`)];

  const rows: string[][] = [];

  if (report.sweep?.results?.length) {
    // Sweep: one row per sweep run
    for (const run of report.sweep.results) {
      const row: string[] = [];
      for (const key of varKeys) {
        row.push(stringifyCsvValue(run.variables?.[key] ?? report.variables?.[key] ?? ""));
      }
      row.push(String(run.score));
      for (const dim of dims) row.push(stringifyCsvValue(run.dimensionScores?.[dim] ?? ""));
      rows.push(row);
    }
  } else {
    // Single run: one data row
    const row: string[] = [];
    for (const key of varKeys) row.push(stringifyCsvValue((report.variables ?? {})[key] ?? ""));
    row.push(String(report.summary.score));
    for (const dim of dims) row.push(stringifyCsvValue((report.summary.dimensionScores ?? {})[dim] ?? ""));
    rows.push(row);
  }

  const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
  const outputPath = join(outputDir, `${stem}_data.csv`);
  writeFileSync(outputPath, csv, "utf-8");
  return { status: "completed", format: "csv", outputPath };
}

function normalizeExportFormat(format?: ExportFormat | string): ExportFormat | null {
  if (!format) return "json";
  if (format === "json" || format === "markdown" || format === "csv") {
    return format;
  }
  return null;
}

function exportFileStem(report: SimulationResult): string {
  return report.id && report.id !== report.name ? report.id : report.name;
}

function collectDimensionKeys(report: SimulationResult): string[] {
  const keys = new Set(Object.keys(report.summary.dimensionScores ?? {}));
  for (const run of report.sweep?.results ?? []) {
    for (const key of Object.keys(run.dimensionScores ?? {})) keys.add(key);
  }
  return [...keys];
}

function collectVariableKeys(report: SimulationResult): string[] {
  const keys = new Set(Object.keys(report.variables ?? {}));
  for (const run of report.sweep?.results ?? []) {
    for (const key of Object.keys(run.variables ?? {})) keys.add(key);
  }
  return [...keys];
}

function stringifyCsvValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
