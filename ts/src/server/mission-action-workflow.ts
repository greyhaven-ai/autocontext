import {
  buildMissionStatusPayload,
  runMissionLoop,
  writeMissionCheckpoint,
} from "../mission/control-plane.js";
import type { LLMProvider } from "../types/index.js";

export type MissionActionName = "run" | "pause" | "resume" | "cancel";

export interface MissionActionRecord {
  id: string;
  metadata?: Record<string, unknown>;
}

export interface MissionActionManager {
  get(missionId: string): MissionActionRecord | null;
  pause(missionId: string): void;
  resume(missionId: string): void;
  cancel(missionId: string): void;
}

export interface MissionActionRunManager {
  getRunsRoot(): string;
  getKnowledgeRoot(): string;
  buildMissionProvider(): LLMProvider;
}

export interface MissionActionWorkflowDeps {
  runMissionLoop: typeof runMissionLoop;
  buildMissionStatusPayload: typeof buildMissionStatusPayload;
  writeMissionCheckpoint: typeof writeMissionCheckpoint;
}

export function buildMissionRunRequest(opts: {
  body: Record<string, unknown>;
  mission: Pick<MissionActionRecord, "metadata">;
  buildMissionProvider: () => LLMProvider;
}): {
  maxIterations: number;
  stepDescription: string | undefined;
  provider: LLMProvider | undefined;
} {
  const maxIterations = typeof opts.body.maxIterations === "number"
    ? opts.body.maxIterations
    : Number.parseInt(String(opts.body.maxIterations ?? "1"), 10);
  const missionType = opts.mission.metadata?.missionType;
  return {
    maxIterations: Number.isInteger(maxIterations) && maxIterations > 0 ? maxIterations : 1,
    stepDescription: typeof opts.body.stepDescription === "string" ? opts.body.stepDescription : undefined,
    provider: missionType !== "code" && missionType !== "proof"
      ? opts.buildMissionProvider()
      : undefined,
  };
}

export async function executeMissionActionRequest(opts: {
  action: MissionActionName;
  missionId: string;
  body: Record<string, unknown>;
  missionManager: MissionActionManager;
  runManager: MissionActionRunManager;
  deps?: Partial<MissionActionWorkflowDeps>;
}): Promise<{ status: number; body: Record<string, unknown> }> {
  const mission = opts.missionManager.get(opts.missionId);
  if (!mission) {
    return {
      status: 404,
      body: { error: `Mission '${opts.missionId}' not found` },
    };
  }

  const deps: MissionActionWorkflowDeps = {
    runMissionLoop: opts.deps?.runMissionLoop ?? runMissionLoop,
    buildMissionStatusPayload: opts.deps?.buildMissionStatusPayload ?? buildMissionStatusPayload,
    writeMissionCheckpoint: opts.deps?.writeMissionCheckpoint ?? writeMissionCheckpoint,
  };

  if (opts.action === "run") {
    const runRequest = buildMissionRunRequest({
      body: opts.body,
      mission,
      buildMissionProvider: () => opts.runManager.buildMissionProvider(),
    });
    return {
      status: 200,
      body: await deps.runMissionLoop(
        opts.missionManager as never,
        opts.missionId,
        opts.runManager.getRunsRoot(),
        opts.runManager.getKnowledgeRoot(),
        runRequest,
      ),
    };
  }

  if (opts.action === "pause") {
    opts.missionManager.pause(mission.id);
  } else if (opts.action === "resume") {
    opts.missionManager.resume(mission.id);
  } else {
    opts.missionManager.cancel(mission.id);
  }

  const checkpointPath = deps.writeMissionCheckpoint(
    opts.missionManager as never,
    mission.id,
    opts.runManager.getRunsRoot(),
  );
  return {
    status: 200,
    body: {
      ...deps.buildMissionStatusPayload(opts.missionManager as never, mission.id),
      checkpointPath,
    },
  };
}
