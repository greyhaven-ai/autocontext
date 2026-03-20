/**
 * Game scenario interface — ScenarioInterface ABC and data types (AC-343 Task 5).
 * Mirrors Python's autocontext/scenarios/base.py.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Data types (Zod schemas)
// ---------------------------------------------------------------------------

export const ObservationSchema = z.object({
  narrative: z.string(),
  state: z.record(z.unknown()).default({}),
  constraints: z.array(z.string()).default([]),
});

export type Observation = z.infer<typeof ObservationSchema>;

export const ResultSchema = z
  .object({
    score: z.number(),
    winner: z.string().nullable().default(null),
    summary: z.string(),
    replay: z.array(z.record(z.unknown())).default([]),
    metrics: z.record(z.number()).default({}),
    validationErrors: z.array(z.string()).default([]),
  })
  .transform((val) => ({
    ...val,
    get passedValidation() {
      return val.validationErrors.length === 0;
    },
  }));

export type Result = z.infer<typeof ResultSchema>;

export const ReplayEnvelopeSchema = z.object({
  scenario: z.string(),
  seed: z.number().int(),
  narrative: z.string(),
  timeline: z.array(z.record(z.unknown())).default([]),
});

export type ReplayEnvelope = z.infer<typeof ReplayEnvelopeSchema>;

export const ExecutionLimitsSchema = z.object({
  timeoutSeconds: z.number().default(10.0),
  maxMemoryMb: z.number().int().default(512),
  networkAccess: z.boolean().default(false),
});

export type ExecutionLimits = z.infer<typeof ExecutionLimitsSchema>;

// ---------------------------------------------------------------------------
// ScenarioInterface — abstract base for game scenarios
// ---------------------------------------------------------------------------

export interface ScoringDimension {
  name: string;
  weight: number;
  description: string;
}

export interface LegalAction {
  action: string;
  description: string;
  type?: string;
  range?: [number, number];
}

/**
 * ScenarioInterface — pluggable game scenario contract.
 * Mirrors Python's ScenarioInterface ABC.
 */
export interface ScenarioInterface {
  readonly name: string;

  describeRules(): string;
  describeStrategyInterface(): string;
  describeEvaluationCriteria(): string;

  initialState(seed?: number): Record<string, unknown>;
  getObservation(state: Record<string, unknown>, playerId: string): Observation;
  validateActions(
    state: Record<string, unknown>,
    playerId: string,
    actions: Record<string, unknown>,
  ): [boolean, string];
  step(state: Record<string, unknown>, actions: Record<string, unknown>): Record<string, unknown>;
  isTerminal(state: Record<string, unknown>): boolean;
  getResult(state: Record<string, unknown>): Result;
  replayToNarrative(replay: Array<Record<string, unknown>>): string;
  renderFrame(state: Record<string, unknown>): Record<string, unknown>;

  // Optional methods with defaults
  enumerateLegalActions(state: Record<string, unknown>): LegalAction[] | null;
  scoringDimensions(): ScoringDimension[] | null;
  executeMatch(strategy: Record<string, unknown>, seed: number): Result;
}
