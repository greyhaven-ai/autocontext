import type {
  EvalReconciliationCounts,
  EvalReconciliationView,
  EvalRunReconciliation,
  EvalTrial,
} from "../contract/types.js";

export interface ReconcileEvalTrialsOptions {
  readonly view: EvalReconciliationView;
}

type OrderedTrial = {
  readonly trial: EvalTrial;
  readonly index: number;
};

export function reconcileEvalTrials(
  trials: readonly EvalTrial[],
  options: ReconcileEvalTrialsOptions,
): EvalRunReconciliation {
  const ordered = orderTrials(trials);
  const taskIds = collectTaskIds(ordered);
  const selected =
    options.view === "best-of-k"
      ? selectBestOfK(ordered, taskIds)
      : selectFirstCompleted(ordered);
  const ignoredTrialIds = collectIgnoredTrialIds(ordered, selected);
  const unresolvedTaskIds = taskIds.filter((taskId) => !selected.has(taskId));
  const counts = countTrials(taskIds, selected, ignoredTrialIds, ordered);

  return {
    view: options.view,
    score: counts.selectedTaskCount === 0 ? 0 : counts.passed / counts.selectedTaskCount,
    selectedTrialIdsByTask: selectedTrialRecord(selected, taskIds),
    ignoredTrialIds,
    unresolvedTaskIds,
    counts,
  };
}

function orderTrials(trials: readonly EvalTrial[]): readonly OrderedTrial[] {
  return trials
    .map((trial, index) => ({ trial, index }))
    .sort((left, right) => {
      const byAttempt = left.trial.attempt - right.trial.attempt;
      return byAttempt === 0 ? left.index - right.index : byAttempt;
    });
}

function collectTaskIds(trials: readonly OrderedTrial[]): readonly string[] {
  const taskIds = new Set<string>();
  for (const { trial } of trials) {
    taskIds.add(trial.taskId);
  }
  return [...taskIds];
}

function selectFirstCompleted(trials: readonly OrderedTrial[]): ReadonlyMap<string, EvalTrial> {
  const selected = new Map<string, EvalTrial>();
  for (const { trial } of trials) {
    if (selected.has(trial.taskId) || !isScored(trial)) continue;
    selected.set(trial.taskId, trial);
  }
  return selected;
}

function selectBestOfK(
  trials: readonly OrderedTrial[],
  taskIds: readonly string[],
): ReadonlyMap<string, EvalTrial> {
  const selected = new Map<string, EvalTrial>();
  for (const taskId of taskIds) {
    const taskTrials = trials
      .map(({ trial }) => trial)
      .filter((trial) => trial.taskId === taskId && isScored(trial));
    const passed = taskTrials.find((trial) => trial.status === "passed");
    const failed = taskTrials.find((trial) => trial.status === "failed");
    const selectedTrial = passed ?? failed;
    if (selectedTrial !== undefined) {
      selected.set(taskId, selectedTrial);
    }
  }
  return selected;
}

function collectIgnoredTrialIds(
  trials: readonly OrderedTrial[],
  selected: ReadonlyMap<string, EvalTrial>,
): readonly string[] {
  const ignored: string[] = [];
  for (const { trial } of trials) {
    if (!isScored(trial)) continue;
    const selectedTrial = selected.get(trial.taskId);
    if (selectedTrial !== undefined && selectedTrial.trialId !== trial.trialId) {
      ignored.push(trial.trialId);
    }
  }
  return ignored;
}

function countTrials(
  taskIds: readonly string[],
  selected: ReadonlyMap<string, EvalTrial>,
  ignoredTrialIds: readonly string[],
  trials: readonly OrderedTrial[],
): EvalReconciliationCounts {
  const selectedTrials = [...selected.values()];
  return {
    taskCount: taskIds.length,
    selectedTaskCount: selected.size,
    passed: selectedTrials.filter((trial) => trial.status === "passed").length,
    failed: selectedTrials.filter((trial) => trial.status === "failed").length,
    infrastructureErrors: trials.filter(({ trial }) => trial.status === "infrastructure-error").length,
    cancelled: trials.filter(({ trial }) => trial.status === "cancelled").length,
    discarded: trials.filter(({ trial }) => trial.status === "discarded").length,
    duplicatesIgnored: ignoredTrialIds.length,
  };
}

function selectedTrialRecord(
  selected: ReadonlyMap<string, EvalTrial>,
  taskIds: readonly string[],
): Readonly<Record<string, string>> {
  const record: Record<string, string> = {};
  for (const taskId of taskIds) {
    const trial = selected.get(taskId);
    if (trial !== undefined) {
      record[taskId] = trial.trialId;
    }
  }
  return record;
}

function isScored(trial: EvalTrial): boolean {
  return trial.status === "passed" || trial.status === "failed";
}
