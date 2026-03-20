/**
 * Execution supervisor — stable input/output contract (AC-343 Task 8b).
 * Mirrors Python's autocontext/execution/supervisor.py.
 */

import type { ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface } from "../scenarios/game-interface.js";

export interface ExecutionInput {
  strategy: Record<string, unknown>;
  seed: number;
  limits: ExecutionLimits;
}

export interface ExecutionOutput {
  result: Result;
  replay: ReplayEnvelope;
}

export class ExecutionSupervisor {
  run(scenario: ScenarioInterface, payload: ExecutionInput): ExecutionOutput {
    const result = scenario.executeMatch(payload.strategy, payload.seed);
    const replay = {
      scenario: scenario.name,
      seed: payload.seed,
      narrative: scenario.replayToNarrative(result.replay),
      timeline: result.replay,
    };
    return { result, replay };
  }
}
