import { asScenarioName } from "../domain/ids.js";
import type { ArtifactStore } from "../knowledge/artifact-store.js";
import type { SQLiteStore } from "../storage/index.js";
import {
  buildTrajectorySnippet,
  extractTrainingHints,
  resolveTrainingPromptContext,
} from "./export-context-workflow.js";
import type {
  ExportOpts,
  ExportProgress,
  ExportRunRef,
  MatchRecord,
  TrainingExportRecord,
  TrainingRecord,
} from "./export-types.js";

export function resolveTrainingExportRuns(store: SQLiteStore, opts: ExportOpts): ExportRunRef[] {
  if (opts.runId) {
    const run = store.getRun(opts.runId);
    if (!run) {
      return [];
    }
    return [{ run_id: run.run_id, scenario: run.scenario }];
  }

  if (opts.scenario) {
    return store.listRunsForScenario(opts.scenario).map((run) => ({
      run_id: run.run_id,
      scenario: run.scenario,
    }));
  }

  return [];
}

export function emitTrainingExportProgress(
  onProgress: ExportOpts["onProgress"],
  progress: ExportProgress,
): void {
  onProgress?.(progress);
}

export function buildTrainingExportRecordsForRun(opts: {
  store: SQLiteStore;
  artifacts: ArtifactStore;
  run: ExportRunRef;
  keptOnly?: boolean;
  includeMatches?: boolean;
  includeQuarantined?: boolean;
  onGenerationRecords?: (
    generationIndex: number,
    generationRecords: TrainingExportRecord[],
  ) => void;
}): TrainingExportRecord[] {
  const records: TrainingExportRecord[] = [];
  const playbook = opts.artifacts.readPlaybook(asScenarioName(opts.run.scenario));
  const hints = extractTrainingHints(playbook);
  const promptContext = resolveTrainingPromptContext(opts.artifacts, opts.run.scenario);
  const generations = opts.store.getGenerations(opts.run.run_id);
  // Trajectory context must not embed scores from generations excluded as untrusted:
  // a quarantined evaluator score would otherwise leak into every later trusted record
  // via its context.trajectory. Filter the trajectory source with the same quarantine
  // policy that gates the strategy records.
  const trajectoryGenerations = opts.includeQuarantined
    ? generations
    : generations.filter((generation) => !generation.quarantined);

  for (const generation of generations) {
    if (opts.keptOnly && generation.gate_decision !== "advance") {
      continue;
    }

    // Quarantined generations are scored under an unpromoted evaluator epoch, so their
    // strategy record is not trusted training data. Their tournament matches carry no
    // evaluator epoch, so they are still emitted below.
    const skipRecord = !opts.includeQuarantined && !!generation.quarantined;

    const generationRecords: TrainingExportRecord[] = [];
    if (!skipRecord) {
      const outputs = opts.store.getAgentOutputs(opts.run.run_id, generation.generation_index);
      const competitorOutput = outputs.find((output) => output.role === "competitor");
      const record: TrainingRecord = {
        run_id: opts.run.run_id,
        scenario: opts.run.scenario,
        generation_index: generation.generation_index,
        strategy: competitorOutput?.content ?? "",
        score: generation.best_score,
        gate_decision: generation.gate_decision,
        context: {
          ...promptContext,
          playbook,
          hints,
          trajectory: buildTrajectorySnippet(trajectoryGenerations, generation.generation_index),
        },
        evaluator_epoch: generation.evaluator_epoch ?? null,
      };
      generationRecords.push(record);
    }

    if (opts.includeMatches) {
      const matches = opts.store.getMatchesForGeneration(
        opts.run.run_id,
        generation.generation_index,
      );
      generationRecords.push(
        ...matches.map((match): MatchRecord => ({
          run_id: opts.run.run_id,
          generation_index: generation.generation_index,
          seed: match.seed,
          score: match.score,
          passed_validation: !!match.passed_validation,
          validation_errors: match.validation_errors,
        })),
      );
    }

    records.push(...generationRecords);
    opts.onGenerationRecords?.(generation.generation_index, generationRecords);
  }

  return records;
}
