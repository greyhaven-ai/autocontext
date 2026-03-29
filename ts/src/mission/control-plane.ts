import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { adaptiveRunMissionLoop } from "./adaptive-executor.js";
import { runUntilDone } from "./executor.js";
import type { MissionManager } from "./manager.js";
import type { Mission, VerifierResult } from "./types.js";
import { rehydrateMissionVerifier } from "./verifiers.js";
import type { LLMProvider } from "../types/index.js";

export function missionCheckpointDir(runsRoot: string, missionId: string): string {
  return join(runsRoot, "missions", missionId, "checkpoints");
}

export function requireMission(manager: MissionManager, missionId: string): Mission {
  const mission = manager.get(missionId);
  if (!mission) {
    throw new Error(`Mission not found: ${missionId}`);
  }
  return mission;
}

export function buildMissionStatusPayload(manager: MissionManager, missionId: string): Record<string, unknown> {
  const mission = requireMission(manager, missionId);
  const steps = manager.steps(missionId);
  const subgoals = manager.subgoals(missionId);
  const verifications = manager.verifications(missionId);
  return {
    ...mission,
    stepsCount: steps.length,
    subgoalCount: subgoals.length,
    verificationCount: verifications.length,
    budgetUsage: manager.budgetUsage(missionId),
    latestVerification: verifications.at(-1) ?? null,
  };
}

export function buildMissionResultPayload(manager: MissionManager, missionId: string): Record<string, unknown> {
  const mission = requireMission(manager, missionId);
  const steps = manager.steps(missionId);
  const subgoals = manager.subgoals(missionId);
  const verifications = manager.verifications(missionId);
  return {
    mission,
    steps,
    subgoals,
    verifications,
    budgetUsage: manager.budgetUsage(missionId),
    latestVerification: verifications.at(-1) ?? null,
  };
}

export function buildMissionArtifactsPayload(
  manager: MissionManager,
  missionId: string,
  runsRoot: string,
): Record<string, unknown> {
  const mission = requireMission(manager, missionId);
  const checkpointDir = missionCheckpointDir(runsRoot, missionId);
  const checkpoints = existsSync(checkpointDir)
    ? readdirSync(checkpointDir)
      .filter((name) => name.endsWith(".json"))
      .sort()
      .reverse()
      .map((name) => {
        const path = join(checkpointDir, name);
        const stats = statSync(path);
        return {
          name,
          path,
          sizeBytes: stats.size,
          updatedAt: stats.mtime.toISOString(),
        };
      })
    : [];

  return {
    missionId: mission.id,
    status: mission.status,
    checkpointDir,
    checkpoints,
    latestCheckpoint: checkpoints[0]
      ? JSON.parse(readFileSync(checkpoints[0].path, "utf-8")) as Record<string, unknown>
      : null,
  };
}

export function writeMissionCheckpoint(manager: MissionManager, missionId: string, runsRoot: string): string {
  requireMission(manager, missionId);
  return manager.saveCheckpoint(missionId, missionCheckpointDir(runsRoot, missionId));
}

function buildFallbackVerifier(manager: MissionManager, missionId: string): () => Promise<VerifierResult> {
  return async () => {
    const subgoals = manager.subgoals(missionId);
    if (subgoals.length === 0) {
      return {
        passed: false,
        reason: "No verifier registered",
        suggestions: [],
        metadata: { autoVerifier: "none" },
      };
    }

    const remaining = subgoals.filter((subgoal) => !["completed", "skipped"].includes(subgoal.status));
    if (remaining.length === 0) {
      return {
        passed: true,
        reason: "All subgoals completed",
        suggestions: [],
        metadata: { autoVerifier: "subgoals" },
      };
    }

    return {
      passed: false,
      reason: `${remaining.length} subgoal(s) remaining`,
      suggestions: remaining.slice(0, 3).map((subgoal) => subgoal.description),
      metadata: {
        autoVerifier: "subgoals",
        remainingSubgoalIds: remaining.map((subgoal) => subgoal.id),
      },
    };
  };
}

export async function runMissionLoop(
  manager: MissionManager,
  missionId: string,
  runsRoot: string,
  knowledgeRoot: string,
  opts?: { maxIterations?: number; stepDescription?: string; provider?: LLMProvider },
): Promise<Record<string, unknown>> {
  const mission = requireMission(manager, missionId);
  const maxIterations = opts?.maxIterations ?? 1;
  const missionType = (mission.metadata as Record<string, unknown> | undefined)?.missionType;

  if (!manager.hasVerifier(missionId)) {
    rehydrateMissionVerifier(manager, mission);
  }

  if (!manager.hasVerifier(missionId)) {
    manager.setVerifier(missionId, buildFallbackVerifier(manager, missionId));
  }

  const result = missionType !== "code" && opts?.provider
    ? await adaptiveRunMissionLoop(
        manager,
        missionId,
        opts.provider,
        knowledgeRoot,
        {
          maxIterations,
          stepDescription: opts.stepDescription,
        },
      )
    : await runLegacyMissionLoop(manager, missionId, mission.goal, maxIterations, opts?.stepDescription);

  const latestVerification = manager.verifications(missionId).at(-1) ?? null;
  let finalStatus = result.finalStatus;

  if (
    missionType === "code"
    && latestVerification
    && latestVerification.passed === false
    && result.finalStatus === "active"
  ) {
    manager.setStatus(missionId, "failed");
    finalStatus = "failed";
  }

  const checkpointPath = writeMissionCheckpoint(manager, missionId, runsRoot);
  return {
    id: missionId,
    ...result,
    finalStatus,
    latestVerification,
    checkpointPath,
  };
}

async function runLegacyMissionLoop(
  manager: MissionManager,
  missionId: string,
  goal: string,
  maxIterations: number,
  stepDescription?: string,
) {
  let iteration = 0;
  return runUntilDone(
    manager,
    missionId,
    async (currentMissionId) => {
      iteration += 1;
      const nextSubgoal = manager.subgoals(currentMissionId).find((subgoal) => (
        subgoal.status === "pending" || subgoal.status === "active"
      ));

      if (nextSubgoal) {
        manager.updateSubgoalStatus(nextSubgoal.id, "completed");
        return {
          description: `Completed subgoal: ${nextSubgoal.description}`,
          status: "completed" as const,
        };
      }

      const description = stepDescription?.trim()
        ?? (maxIterations === 1
          ? `Advance mission toward goal: ${goal}`
          : `Advance mission toward goal (${iteration}/${maxIterations}): ${goal}`);
      return {
        description,
        status: "completed" as const,
      };
    },
    { maxIterations },
  );
}
