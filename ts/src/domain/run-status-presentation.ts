export interface RunStatusGenerationPresentation {
  readonly generation: number;
  readonly bestScore: number;
  readonly gateDecision?: string;
}

export interface RunStatusProgressPresentation {
  readonly bestScore: number | null;
  readonly threshold: number;
  readonly latestPassAtK?: {
    readonly k: number;
    readonly passed: boolean;
  };
}

export interface RunStatusPresentation {
  readonly runId: string;
  readonly status: string;
  readonly scenario: string;
  readonly completedGenerations: number;
  readonly targetGenerations: number;
  readonly latestGeneration?: RunStatusGenerationPresentation;
  readonly progress?: RunStatusProgressPresentation;
  readonly runtimeSessionId?: string;
}

/** Shared human-readable status presenter used by the CLI and operator TUI. */
export function renderRunStatusPresentation(status: RunStatusPresentation): string[] {
  return [
    `Run ${status.runId}`,
    `  Status: ${status.status}`,
    `  Scenario: ${status.scenario}`,
    `  Generations: ${status.completedGenerations}/${status.targetGenerations}`,
    ...(status.latestGeneration
      ? [
          `  Latest best score: ${formatScore(status.latestGeneration.bestScore)} (generation ${status.latestGeneration.generation})`,
          ...(status.latestGeneration.gateDecision
            ? [`  Latest gate: ${status.latestGeneration.gateDecision}`]
            : []),
        ]
      : []),
    ...(status.progress ? [`  ${renderProgress(status.progress)}`] : []),
    ...(status.runtimeSessionId ? [`  Runtime session: ${status.runtimeSessionId}`] : []),
  ];
}

function renderProgress(progress: RunStatusProgressPresentation): string {
  return [
    `Progress best score: ${formatNullableScore(progress.bestScore)}`,
    `(threshold ${formatScore(progress.threshold)},`,
    progress.latestPassAtK
      ? `pass@${progress.latestPassAtK.k}: ${progress.latestPassAtK.passed ? "pass" : "miss"})`
      : "pass@k: n/a)",
  ].join(" ");
}

function formatNullableScore(score: number | null): string {
  return score === null ? "n/a" : formatScore(score);
}

function formatScore(score: number): string {
  return Number.isFinite(score) ? score.toFixed(3) : String(score);
}
