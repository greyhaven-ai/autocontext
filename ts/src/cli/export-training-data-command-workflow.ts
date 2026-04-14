import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

import type {
  ExportOpts,
  ExportProgress,
  TrainingExportRecord,
} from "../training/export-types.js";

export const EXPORT_TRAINING_DATA_HELP_TEXT = [
  "autoctx export-training-data --run-id <id> [--scenario <name> --all-runs] [--output <file>] [--include-matches] [--kept-only]",
  "",
  "Exports training data as JSONL with Python-compatible snake_case fields.",
  "",
  "Unsupported Python commands: train, trigger-distillation (require MLX/CUDA backends)",
].join("\n");

export interface ExportTrainingDataCommandValues {
  "run-id"?: string;
  scenario?: string;
  "all-runs"?: boolean;
  output?: string;
  "include-matches"?: boolean;
  "kept-only"?: boolean;
}

export interface ExportTrainingDataCommandPlan {
  runId?: string;
  scenario?: string;
  allRuns: boolean;
  output?: string;
  includeMatches: boolean;
  keptOnly: boolean;
}

export interface ExportTrainingDataCommandResult {
  stdout: string;
  stderrLines: string[];
}

export function planExportTrainingDataCommand(
  values: ExportTrainingDataCommandValues,
): ExportTrainingDataCommandPlan {
  if (!values["run-id"] && !values.scenario) {
    throw new Error("Error: --run-id or --scenario is required");
  }

  if (values.scenario && !values["run-id"] && !values["all-runs"]) {
    throw new Error("Error: --all-runs is required with --scenario");
  }

  return {
    runId: values["run-id"],
    scenario: values.scenario,
    allRuns: Boolean(values["all-runs"]),
    output: values.output,
    includeMatches: Boolean(values["include-matches"]),
    keptOnly: Boolean(values["kept-only"]),
  };
}

export function renderExportTrainingDataProgress(
  progress: ExportProgress,
): string | null {
  if (progress.phase === "start") {
    return `Scanning ${progress.totalRuns} run(s)...`;
  }
  if (progress.phase === "generation" && progress.generationIndex !== undefined) {
    return `Processed run ${progress.runId} generation ${progress.generationIndex} (${progress.recordsEmitted} records)`;
  }
  return null;
}

function writeOutputFileWithParents(path: string, contents: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, contents, "utf-8");
}

export function executeExportTrainingDataCommandWorkflow<Store, Artifacts>(opts: {
  plan: ExportTrainingDataCommandPlan;
  store: Store;
  artifacts: Artifacts;
  exportTrainingData: (
    store: Store,
    artifacts: Artifacts,
    opts: ExportOpts,
  ) => TrainingExportRecord[];
  writeOutputFile?: (path: string, contents: string) => void;
}): ExportTrainingDataCommandResult {
  const stderrLines: string[] = [
    `Exporting training data${opts.plan.runId ? ` for run ${opts.plan.runId}` : ` for scenario ${opts.plan.scenario}`}...`,
  ];

  const records = opts.exportTrainingData(opts.store, opts.artifacts, {
    runId: opts.plan.runId,
    scenario: opts.plan.scenario,
    includeMatches: opts.plan.includeMatches,
    keptOnly: opts.plan.keptOnly,
    onProgress: (progress) => {
      const rendered = renderExportTrainingDataProgress(progress);
      if (rendered) {
        stderrLines.push(rendered);
      }
    },
  });

  const jsonl = records.map((record) => JSON.stringify(record)).join("\n");
  stderrLines.push(`Exported ${records.length} record(s).`);

  if (opts.plan.output) {
    const writeOutputFile = opts.writeOutputFile ?? writeOutputFileWithParents;
    writeOutputFile(opts.plan.output, `${jsonl}\n`);
    return {
      stdout: JSON.stringify({ output: opts.plan.output, records: records.length }),
      stderrLines,
    };
  }

  return { stdout: jsonl, stderrLines };
}
