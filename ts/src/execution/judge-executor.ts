/**
 * JudgeExecutor — evaluates agent output by delegating to AgentTaskInterface.
 * Port of autocontext/src/autocontext/execution/judge_executor.py
 */

import type { AgentTaskInterface, AgentTaskResult } from "../types/index.js";

export class JudgeExecutor {
  private task: AgentTaskInterface;

  constructor(task: AgentTaskInterface) {
    this.task = task;
  }

  /**
   * Evaluate agent output using the task's evaluateOutput method.
   * Runs context preparation and validation before judging.
   */
  async execute(
    agentOutput: string,
    state: Record<string, unknown>,
    opts?: {
      referenceContext?: string;
      requiredConcepts?: string[];
      calibrationExamples?: Array<Record<string, unknown>>;
      pinnedDimensions?: string[];
    },
  ): Promise<AgentTaskResult> {
    // Run context preparation if the task supports it
    const preparedState = this.task.prepareContext
      ? await this.task.prepareContext({ ...state })
      : { ...state };

    // Validate context
    const contextErrors = this.task.validateContext
      ? this.task.validateContext(preparedState)
      : [];

    if (contextErrors.length > 0) {
      return {
        score: 0.0,
        reasoning: `Context validation failed: ${contextErrors.join("; ")}`,
        dimensionScores: {},
        internalRetries: 0,
      };
    }

    return this.task.evaluateOutput(agentOutput, preparedState, opts);
  }
}
