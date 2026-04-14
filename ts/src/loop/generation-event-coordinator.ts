import type { GenerationGateDecision } from "./generation-attempt-state.js";

export interface RunStartedPayload {
  [key: string]: unknown;
  run_id: string;
  scenario: string;
  target_generations: number;
}

export interface GenerationStartedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
}

export interface AgentsStartedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
  roles: Array<"competitor" | "analyst" | "coach" | "curator">;
}

export interface TournamentCompletedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
  mean_score: number;
  best_score: number;
  wins: number;
  losses: number;
}

export interface GateDecidedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
  decision: GenerationGateDecision;
  delta: number;
  threshold: number;
}

export interface GenerationCompletedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
  mean_score: number;
  best_score: number;
  elo: number;
  gate_decision: GenerationGateDecision;
}

export interface RunCompletedPayload {
  [key: string]: unknown;
  run_id: string;
  completed_generations: number;
  best_score: number;
  elo: number;
  session_report_path: string;
  dead_ends_found: number;
}

export interface RunFailedPayload {
  [key: string]: unknown;
  run_id: string;
  error: string;
}

export function buildRunStartedPayload(opts: {
  runId: string;
  scenarioName: string;
  targetGenerations: number;
}): RunStartedPayload {
  return {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.targetGenerations,
  };
}

export function buildGenerationStartedPayload(
  runId: string,
  generation: number,
): GenerationStartedPayload {
  return {
    run_id: runId,
    generation,
  };
}

export function buildAgentsStartedPayload(
  runId: string,
  generation: number,
  curatorEnabled: boolean,
): AgentsStartedPayload {
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
): TournamentCompletedPayload {
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
): GateDecidedPayload {
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
): GenerationCompletedPayload {
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
}): RunCompletedPayload {
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
): RunFailedPayload {
  return {
    run_id: runId,
    error,
  };
}
