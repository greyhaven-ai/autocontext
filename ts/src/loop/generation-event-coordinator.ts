import type { GenerationGateDecision } from "./generation-attempt-state.js";

export function buildRunStartedPayload(opts: {
  runId: string;
  scenarioName: string;
  targetGenerations: number;
}): Record<string, unknown> {
  return {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.targetGenerations,
  };
}

export function buildGenerationStartedPayload(
  runId: string,
  generation: number,
): Record<string, unknown> {
  return {
    run_id: runId,
    generation,
  };
}

export function buildAgentsStartedPayload(
  runId: string,
  generation: number,
  curatorEnabled: boolean,
): Record<string, unknown> {
  return {
    run_id: runId,
    generation,
    roles: curatorEnabled
      ? ["competitor", "analyst", "coach", "curator"]
      : ["competitor", "analyst", "coach"],
  };
}

export function buildTournamentCompletedPayload(
  runId: string,
  generation: number,
  result: {
    meanScore: number;
    bestScore: number;
    wins: number;
    losses: number;
  },
): Record<string, unknown> {
  return {
    run_id: runId,
    generation,
    mean_score: result.meanScore,
    best_score: result.bestScore,
    wins: result.wins,
    losses: result.losses,
  };
}

export function buildGateDecidedPayload(
  runId: string,
  generation: number,
  decision: GenerationGateDecision,
  delta: number,
  threshold: number,
): Record<string, unknown> {
  return {
    run_id: runId,
    generation,
    decision,
    delta,
    threshold,
  };
}

export function buildGenerationCompletedPayload(
  runId: string,
  generation: number,
  result: {
    meanScore: number;
    bestScore: number;
    elo: number;
    gateDecision: GenerationGateDecision;
  },
): Record<string, unknown> {
  return {
    run_id: runId,
    generation,
    mean_score: result.meanScore,
    best_score: result.bestScore,
    elo: result.elo,
    gate_decision: result.gateDecision,
  };
}

export function buildRunCompletedPayload(opts: {
  runId: string;
  completedGenerations: number;
  bestScore: number;
  currentElo: number;
  sessionReportPath: string;
  deadEndsFound: number;
}): Record<string, unknown> {
  return {
    run_id: opts.runId,
    completed_generations: opts.completedGenerations,
    best_score: opts.bestScore,
    elo: opts.currentElo,
    session_report_path: opts.sessionReportPath,
    dead_ends_found: opts.deadEndsFound,
  };
}

export function buildRunFailedPayload(
  runId: string,
  error: string,
): Record<string, unknown> {
  return {
    run_id: runId,
    error,
  };
}
