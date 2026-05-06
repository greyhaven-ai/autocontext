export type TaskBudgetAction = "continue" | "write-artifact" | "stop";

export interface TaskBudgetCheckpoint {
  readonly name: string;
  readonly atFraction: number;
  readonly requiresArtifact?: boolean;
}

export interface TaskBudgetInputs {
  readonly elapsedMs: number;
  readonly totalBudgetMs: number;
  readonly artifactWritten: boolean;
  readonly checkpoints: readonly TaskBudgetCheckpoint[];
}

export interface TaskBudgetDecision {
  readonly action: TaskBudgetAction;
  readonly reasons: readonly string[];
}

export function evaluateTaskBudget(inputs: TaskBudgetInputs): TaskBudgetDecision {
  if (inputs.totalBudgetMs <= 0 || inputs.elapsedMs >= inputs.totalBudgetMs) {
    return { action: "stop", reasons: ["task budget exhausted"] };
  }

  const elapsedFraction = inputs.elapsedMs / inputs.totalBudgetMs;
  const reasons = inputs.checkpoints
    .filter((checkpoint) => checkpoint.requiresArtifact === true)
    .filter((checkpoint) => !inputs.artifactWritten && elapsedFraction >= checkpoint.atFraction)
    .map(
      (checkpoint) =>
        `checkpoint ${checkpoint.name} requires an artifact by ${formatPercent(checkpoint.atFraction)}`,
    );

  if (reasons.length > 0) {
    return { action: "write-artifact", reasons };
  }

  return { action: "continue", reasons: [] };
}

function formatPercent(fraction: number): string {
  const percent = fraction * 100;
  return Number.isInteger(percent) ? `${percent}%` : `${Number(percent.toFixed(2))}%`;
}
