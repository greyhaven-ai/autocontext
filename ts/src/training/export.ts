/**
 * Training data export — Python-compatible JSONL format (AC-366).
 * Mirrors Python's autocontext/training/export.py + types.py.
 *
 * Field names use snake_case to match the Python contract so that
 * downstream training pipelines can consume TS-generated data without
 * field-name translation.
 */

import type { ArtifactStore } from "../knowledge/artifact-store.js";
import type { SQLiteStore } from "../storage/index.js";
import {
  buildTrainingExportRecordsForRun,
  emitTrainingExportProgress,
  resolveTrainingExportRuns,
} from "./export-records-workflow.js";
import type {
  ExportOpts,
  TrainingExportRecord,
} from "./export-types.js";

export type {
  ExportOpts,
  ExportProgress,
  MatchRecord,
  TrainingExportRecord,
  TrainingRecord,
} from "./export-types.js";

export function exportTrainingData(
  store: SQLiteStore,
  artifacts: ArtifactStore,
  opts: ExportOpts,
): TrainingExportRecord[] {
  const records: TrainingExportRecord[] = [];
  const runs = resolveTrainingExportRuns(store, opts);

  emitTrainingExportProgress(opts.onProgress, {
    phase: "start",
    totalRuns: runs.length,
    runIndex: 0,
    runId: "",
    scenario: opts.scenario ?? "",
    recordsEmitted: records.length,
  });

  for (const [runIndex, run] of runs.entries()) {
    emitTrainingExportProgress(opts.onProgress, {
      phase: "run",
      totalRuns: runs.length,
      runIndex: runIndex + 1,
      runId: run.run_id,
      scenario: run.scenario,
      recordsEmitted: records.length,
    });

    const runRecords = buildTrainingExportRecordsForRun({
      store,
      artifacts,
      run,
      keptOnly: opts.keptOnly,
      includeMatches: opts.includeMatches,
      onGenerationRecords: (generationIndex, generationRecords) => {
        records.push(...generationRecords);
        emitTrainingExportProgress(opts.onProgress, {
          phase: "generation",
          totalRuns: runs.length,
          runIndex: runIndex + 1,
          runId: run.run_id,
          scenario: run.scenario,
          generationIndex,
          recordsEmitted: records.length,
        });
      },
    });

    if (runRecords.length === 0) {
      continue;
    }
  }

  return records;
}
