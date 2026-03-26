/**
 * Mission step executor — bounded execution loop (AC-412).
 *
 * Executes one step at a time, checks budget, invokes verifier,
 * and handles blocked/exhausted states honestly.
 */

import type { MissionManager } from "./manager.js";
import type { MissionStatus } from "./types.js";

export interface StepResult {
  description: string;
  status: "completed" | "failed" | "blocked";
  blockReason?: string;
}

export interface RunStepResult {
  stepRecorded: boolean;
  budgetExhausted: boolean;
  blocked: boolean;
  finalStatus?: MissionStatus;
  error?: string;
}

export interface RunUntilDoneResult {
  finalStatus: MissionStatus;
  stepsExecuted: number;
  verifierPassed: boolean;
}

export type StepExecutor = (missionId: string) => Promise<StepResult>;

/**
 * Execute a single bounded step within a mission.
 * Checks budget before execution. Records result.
 */
export async function runStep(
  manager: MissionManager,
  missionId: string,
  executor: StepExecutor,
): Promise<RunStepResult> {
  const mission = manager.get(missionId);
  if (!mission) {
    throw new Error(`Mission not found: ${missionId}`);
  }

  if (mission.status !== "active") {
    return {
      stepRecorded: false,
      budgetExhausted: mission.status === "budget_exhausted",
      blocked: mission.status === "blocked",
      finalStatus: mission.status,
      error: `Mission is ${mission.status}`,
    };
  }

  // Check budget before executing
  const budget = manager.budgetUsage(missionId);
  if (budget.exhausted) {
    manager.setStatus(missionId, "budget_exhausted");
    return { stepRecorded: false, budgetExhausted: true, blocked: false, finalStatus: "budget_exhausted" };
  }

  try {
    const result = await executor(missionId);

    // Record the step
    const stepId = manager.advance(missionId, result.description);

    if (result.status === "failed") {
      manager.updateStep(stepId, "failed", result.description);
      return { stepRecorded: true, budgetExhausted: false, blocked: false, error: result.description };
    }

    if (result.status === "blocked") {
      manager.updateStep(stepId, "blocked", result.blockReason ?? result.description);
      manager.setStatus(missionId, "blocked");
      return {
        stepRecorded: true,
        budgetExhausted: false,
        blocked: true,
        finalStatus: "blocked",
        error: result.blockReason ?? result.description,
      };
    }

    // Check budget after step (may have just hit the limit)
    const updatedBudget = manager.budgetUsage(missionId);
    if (updatedBudget.exhausted) {
      manager.setStatus(missionId, "budget_exhausted");
      return { stepRecorded: true, budgetExhausted: true, blocked: false, finalStatus: "budget_exhausted" };
    }

    return { stepRecorded: true, budgetExhausted: false, blocked: false };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const stepId = manager.advance(missionId, `Error: ${message}`);
    manager.updateStep(stepId, "failed", message);
    return { stepRecorded: true, budgetExhausted: false, blocked: false, error: message };
  }
}

/**
 * Run steps in a loop until verifier passes, budget exhausted, or blocked.
 */
export async function runUntilDone(
  manager: MissionManager,
  missionId: string,
  executor: StepExecutor,
  opts?: { maxIterations?: number },
): Promise<RunUntilDoneResult> {
  const maxIterations = opts?.maxIterations ?? 100;
  let stepsExecuted = 0;

  for (let i = 0; i < maxIterations; i++) {
    const mission = manager.get(missionId);
    if (!mission) {
      throw new Error(`Mission not found: ${missionId}`);
    }
    if (mission.status !== "active") {
      return {
        finalStatus: mission.status,
        stepsExecuted,
        verifierPassed: false,
      };
    }

    const stepResult = await runStep(manager, missionId, executor);
    if (stepResult.stepRecorded) stepsExecuted++;

    if (stepResult.finalStatus && stepResult.finalStatus !== "active") {
      return {
        finalStatus: stepResult.finalStatus,
        stepsExecuted,
        verifierPassed: false,
      };
    }

    if (stepResult.budgetExhausted) {
      return {
        finalStatus: "budget_exhausted",
        stepsExecuted,
        verifierPassed: false,
      };
    }

    if (stepResult.blocked) {
      return {
        finalStatus: "blocked",
        stepsExecuted,
        verifierPassed: false,
      };
    }

    // After each step, check verifier
    const verifyResult = await manager.verify(missionId);
    if (verifyResult.passed) {
      return {
        finalStatus: "completed",
        stepsExecuted,
        verifierPassed: true,
      };
    }
    if (verifyResult.metadata?.verifierThrew === true) {
      manager.setStatus(missionId, "verifier_failed");
      return {
        finalStatus: "verifier_failed",
        stepsExecuted,
        verifierPassed: false,
      };
    }
  }

  // Max iterations reached without completion
  const mission = manager.get(missionId);
  return {
    finalStatus: (mission?.status ?? "active") as MissionStatus,
    stepsExecuted,
    verifierPassed: false,
  };
}
