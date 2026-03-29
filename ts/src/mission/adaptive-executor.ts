/**
 * Adaptive mission executor — LLM-driven execution loop (AC-435).
 *
 * Replaces the old generic "Advance mission toward goal" bookkeeping
 * with real adaptive planning:
 *
 * 1. Decompose the mission goal into subgoals via LLM planner
 * 2. Plan each step based on goal + history + verifier feedback
 * 3. Execute the planned step (record in mission store)
 * 4. Check verifier after each step
 * 5. Revise plan when verifier feedback suggests changes
 * 6. Continue until success, failure, budget exhaustion, or block
 */

import type { LLMProvider } from "../types/index.js";
import type { MissionManager } from "./manager.js";
import type { MissionStatus, VerifierResult } from "./types.js";
import { type SubgoalPlan } from "./planner.js";
import { SimulationAwarePlanner, type SimulationStepPlan } from "./simulation-bridge.js";
import { rehydrateMissionVerifier } from "./verifiers.js";

export interface AdaptiveRunOpts {
  maxIterations?: number;
  stepDescription?: string;
}

export interface AdaptiveRunResult {
  finalStatus: MissionStatus;
  stepsExecuted: number;
  verifierPassed: boolean;
  planGenerated: boolean;
  latestVerification: VerifierResult | null;
  checkpointPath?: string;
}

/**
 * Run a mission with adaptive LLM-driven planning.
 *
 * This is the AC-435 replacement for the old runMissionLoop().
 * Instead of generic "advance toward goal" steps, it:
 * - Decomposes the goal into subgoals
 * - Plans each step based on context
 * - Revises the plan based on verifier feedback
 */
export async function adaptiveRunMissionLoop(
  manager: MissionManager,
  missionId: string,
  provider: LLMProvider,
  knowledgeRoot: string,
  opts?: AdaptiveRunOpts,
): Promise<AdaptiveRunResult> {
  const mission = manager.get(missionId);
  if (!mission) throw new Error(`Mission not found: ${missionId}`);
  if (mission.status !== "active") {
    return {
      finalStatus: mission.status,
      stepsExecuted: 0,
      verifierPassed: false,
      planGenerated: false,
      latestVerification: null,
    };
  }

  const maxIterations = opts?.maxIterations ?? 10;
  const planner = new SimulationAwarePlanner(provider, knowledgeRoot);

  // Ensure verifier is registered
  if (!manager.hasVerifier(missionId)) {
    rehydrateMissionVerifier(manager, mission);
  }
  if (!manager.hasVerifier(missionId)) {
    manager.setVerifier(missionId, buildSubgoalVerifier(manager, missionId));
  }

  // Step 1: Decompose goal into subgoals (if none exist yet)
  let planGenerated = false;
  const existingSubgoals = manager.subgoals(missionId);
  if (existingSubgoals.length === 0) {
    const plan = await planner.decompose(mission.goal);
    applySubgoals(manager, missionId, plan.subgoals);
    planGenerated = true;
  }

  // Step 2: Adaptive execution loop
  let stepsExecuted = 0;
  let latestVerification: VerifierResult | null = null;

  for (let i = 0; i < maxIterations; i++) {
    const currentMission = manager.get(missionId);
    if (!currentMission || currentMission.status !== "active") break;

    // Check budget
    const budget = manager.budgetUsage(missionId);
    if (budget.exhausted) {
      manager.setStatus(missionId, "budget_exhausted");
      break;
    }

    // Gather context for planning
    const steps = manager.steps(missionId);
    const completedSteps = steps.filter((s) => s.status === "completed").map((s) => s.description);
    const subgoals = manager.subgoals(missionId);
    const remainingSubgoals = subgoals
      .filter((s) => s.status === "pending" || s.status === "active")
      .map((s) => s.description);

    const verifierFeedback = latestVerification
      ? {
          passed: latestVerification.passed,
          reason: latestVerification.reason,
          suggestions: latestVerification.suggestions ?? [],
        }
      : undefined;

    // Plan next step
    const stepPlan: SimulationStepPlan = i === 0 && opts?.stepDescription?.trim()
      ? {
          description: opts.stepDescription.trim(),
          reasoning: "Operator-provided step override",
          shouldRevise: false,
          ...(remainingSubgoals.length === 1 ? { targetSubgoal: remainingSubgoals[0] } : {}),
        }
      : await planner.planAndSimulate({
          goal: mission.goal,
          completedSteps,
          remainingSubgoals,
          verifierFeedback,
        });

    // Apply plan revision if needed
    if (stepPlan.shouldRevise && stepPlan.revisedSubgoals?.length) {
      replacePendingSubgoals(manager, missionId, stepPlan.revisedSubgoals);
    }

    if (stepPlan.simulateFirst?.description) {
      const simulationStepId = manager.advance(
        missionId,
        `Simulate: ${stepPlan.simulateFirst.description}`,
      );
      manager.updateStep(
        simulationStepId,
        stepPlan.simulationResult?.status === "failed" ? "failed" : "completed",
        describeSimulationStep(stepPlan),
      );
      stepsExecuted++;

      const postSimulationBudget = manager.budgetUsage(missionId);
      if (postSimulationBudget.exhausted) {
        manager.setStatus(missionId, "budget_exhausted");
        break;
      }
    }

    // Execute the step (record it)
    const stepId = manager.advance(missionId, stepPlan.description);
    manager.updateStep(stepId, "completed", stepPlan.reasoning);
    stepsExecuted++;

    // Mark matching subgoal as completed
    const currentSubgoals = manager.subgoals(missionId);
    const matchingSubgoal = stepPlan.targetSubgoal
      ? currentSubgoals.find(
          (s) =>
            (s.status === "pending" || s.status === "active")
            && s.description === stepPlan.targetSubgoal,
        )
      : undefined;
    if (matchingSubgoal) {
      manager.updateSubgoalStatus(matchingSubgoal.id, "completed");
    }

    // Verify after step
    latestVerification = await manager.verify(missionId);
    if (latestVerification.passed) {
      break;
    }
  }

  // Final state
  const finalMission = manager.get(missionId);
  const finalStatus = finalMission?.status ?? "active";
  return {
    finalStatus: finalStatus as MissionStatus,
    stepsExecuted,
    verifierPassed: latestVerification?.passed ?? false,
    planGenerated,
    latestVerification,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function applySubgoals(manager: MissionManager, missionId: string, subgoals: SubgoalPlan[]): void {
  for (const sg of subgoals) {
    manager.addSubgoal(missionId, { description: sg.description, priority: sg.priority });
  }
}

function replacePendingSubgoals(manager: MissionManager, missionId: string, subgoals: SubgoalPlan[]): void {
  for (const existing of manager.subgoals(missionId)) {
    if (existing.status === "pending" || existing.status === "active") {
      manager.updateSubgoalStatus(existing.id, "skipped");
    }
  }
  applySubgoals(manager, missionId, subgoals);
}

function buildSubgoalVerifier(manager: MissionManager, missionId: string): () => Promise<VerifierResult> {
  return async () => {
    const subgoals = manager.subgoals(missionId);
    if (subgoals.length === 0) {
      return { passed: false, reason: "No subgoals defined", suggestions: [], metadata: {} };
    }
    const remaining = subgoals.filter((s) => !["completed", "skipped"].includes(s.status));
    if (remaining.length === 0) {
      return { passed: true, reason: "All subgoals completed", suggestions: [], metadata: {} };
    }
    return {
      passed: false,
      reason: `${remaining.length} subgoal(s) remaining`,
      suggestions: remaining.slice(0, 3).map((s) => s.description),
      metadata: {},
    };
  };
}

function describeSimulationStep(stepPlan: SimulationStepPlan): string {
  if (!stepPlan.simulateFirst?.description) {
    return "Simulation requested";
  }

  if (!stepPlan.simulationResult) {
    return JSON.stringify({
      description: stepPlan.simulateFirst.description,
      status: "failed",
      error: "Simulation did not complete",
    });
  }

  return JSON.stringify({
    id: stepPlan.simulationResult.id,
    name: stepPlan.simulationResult.name,
    status: stepPlan.simulationResult.status,
    description: stepPlan.simulateFirst.description,
    score: stepPlan.simulationResult.summary.score,
    reportPath: stepPlan.simulationResult.artifacts.reportPath ?? null,
  });
}
