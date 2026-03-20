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

export interface ExecutionEngine {
  execute(
    scenario: ScenarioInterface,
    strategy: Record<string, unknown>,
    seed: number,
    limits: ExecutionLimits,
  ): ExecutionOutput;
}

export class LocalExecutor implements ExecutionEngine {
  execute(
    scenario: ScenarioInterface,
    strategy: Record<string, unknown>,
    seed: number,
    limits: ExecutionLimits,
  ): ExecutionOutput {
    const startedAt = Date.now();
    const result = scenario.executeMatch(strategy, seed);
    const elapsedSeconds = (Date.now() - startedAt) / 1000;

    if (elapsedSeconds > limits.timeoutSeconds) {
      throw new Error(`strategy execution exceeded ${limits.timeoutSeconds}s`);
    }

    const replay = {
      scenario: scenario.name,
      seed,
      narrative: scenario.replayToNarrative(result.replay),
      timeline: result.replay,
    };
    return { result, replay };
  }
}

export class ExecutionSupervisor {
  constructor(private readonly executor: ExecutionEngine = new LocalExecutor()) {}

  run(scenario: ScenarioInterface, payload: ExecutionInput): ExecutionOutput {
    return this.executor.execute(
      scenario,
      payload.strategy,
      payload.seed,
      payload.limits,
    );
  }
}
