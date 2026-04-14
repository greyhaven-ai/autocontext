import type { LLMProvider } from "../types/index.js";
import type { MissionBudget, MissionStatus } from "../mission/types.js";
import type {
  planMissionCreate,
  planMissionRun,
} from "./mission-command-workflow.js";

import { buildMissionCheckpointPayload } from "./mission-command-workflow.js";

type MissionCreatePlan = ReturnType<typeof planMissionCreate>;
type MissionRunPlan = ReturnType<typeof planMissionRun>;
type MissionLifecycleAction = "pause" | "resume" | "cancel";

type MissionCheckpointBudget = Pick<MissionBudget, "maxSteps">;

type GenericMissionCreateInput = {
  name: string;
  goal: string;
  budget?: MissionCheckpointBudget;
};

type CodeMissionCreateInput = GenericMissionCreateInput & {
  repoPath: string;
  testCommand: string;
  lintCommand?: string;
  buildCommand?: string;
  metadata: Record<string, unknown>;
};

function buildMissionCheckpointResult<TPayload extends Record<string, unknown>, TManager>(opts: {
  manager: TManager;
  missionId: string;
  runsRoot: string;
  buildMissionStatusPayload(manager: TManager, missionId: string): TPayload;
  writeMissionCheckpoint(manager: TManager, missionId: string, runsRoot: string): string;
}): TPayload & { checkpointPath: string } {
  return buildMissionCheckpointPayload(
    opts.buildMissionStatusPayload(opts.manager, opts.missionId),
    opts.writeMissionCheckpoint(opts.manager, opts.missionId, opts.runsRoot),
  );
}

export function executeMissionCreateCommand<TManager, TPayload extends Record<string, unknown>>(opts: {
  manager: TManager & {
    create(input: GenericMissionCreateInput): string;
  };
  createCodeMission(manager: TManager, spec: CodeMissionCreateInput): string;
  buildMissionStatusPayload(manager: TManager, missionId: string): TPayload;
  writeMissionCheckpoint(manager: TManager, missionId: string, runsRoot: string): string;
  runsRoot: string;
  plan: MissionCreatePlan;
}): TPayload & { checkpointPath: string } {
  const missionId = opts.plan.missionType === "code"
    ? opts.createCodeMission(opts.manager, {
        name: opts.plan.name,
        goal: opts.plan.goal,
        repoPath: opts.plan.repoPath,
        testCommand: opts.plan.testCommand,
        lintCommand: opts.plan.lintCommand,
        buildCommand: opts.plan.buildCommand,
        budget: opts.plan.budget,
        metadata: {},
      })
    : opts.manager.create({
        name: opts.plan.name,
        goal: opts.plan.goal,
        budget: opts.plan.budget,
      });

  return buildMissionCheckpointResult({
    manager: opts.manager,
    missionId,
    runsRoot: opts.runsRoot,
    buildMissionStatusPayload: opts.buildMissionStatusPayload,
    writeMissionCheckpoint: opts.writeMissionCheckpoint,
  });
}

export async function executeMissionRunCommand<
  TManager,
  TProvider extends LLMProvider | undefined,
  TResult,
>(opts: {
  manager: TManager;
  plan: MissionRunPlan;
  runsRoot: string;
  knowledgeRoot: string;
  createAdaptiveProvider(): TProvider | Promise<TProvider>;
  runMissionLoop(
    manager: TManager,
    missionId: string,
    runsRoot: string,
    knowledgeRoot: string,
    options: {
      maxIterations: number;
      stepDescription?: string;
      provider?: TProvider;
    },
  ): Promise<TResult>;
}): Promise<TResult> {
  const provider = opts.plan.needsAdaptivePlanning
    ? await opts.createAdaptiveProvider()
    : undefined;

  return opts.runMissionLoop(
    opts.manager,
    opts.plan.id,
    opts.runsRoot,
    opts.knowledgeRoot,
    {
      maxIterations: opts.plan.maxIterations,
      stepDescription: opts.plan.stepDescription,
      provider,
    },
  );
}

export function executeMissionStatusCommand<TManager, TPayload>(opts: {
  manager: TManager;
  missionId: string;
  buildMissionStatusPayload(manager: TManager, missionId: string): TPayload;
}): TPayload {
  return opts.buildMissionStatusPayload(opts.manager, opts.missionId);
}

export function executeMissionListCommand<TMission>(opts: {
  listMissions(status?: MissionStatus): TMission[];
  status?: MissionStatus;
}): TMission[] {
  return opts.listMissions(opts.status);
}

export function executeMissionArtifactsCommand<TManager, TPayload>(opts: {
  manager: TManager;
  missionId: string;
  runsRoot: string;
  buildMissionArtifactsPayload(
    manager: TManager,
    missionId: string,
    runsRoot: string,
  ): TPayload;
}): TPayload {
  return opts.buildMissionArtifactsPayload(
    opts.manager,
    opts.missionId,
    opts.runsRoot,
  );
}

export function executeMissionLifecycleCommand<
  TManager extends Record<MissionLifecycleAction, (missionId: string) => void>,
  TPayload extends Record<string, unknown>,
>(opts: {
  action: MissionLifecycleAction;
  missionId: string;
  manager: TManager;
  buildMissionStatusPayload(manager: TManager, missionId: string): TPayload;
  writeMissionCheckpoint(manager: TManager, missionId: string, runsRoot: string): string;
  runsRoot: string;
}): TPayload & { checkpointPath: string } {
  opts.manager[opts.action](opts.missionId);
  return buildMissionCheckpointResult({
    manager: opts.manager,
    missionId: opts.missionId,
    runsRoot: opts.runsRoot,
    buildMissionStatusPayload: opts.buildMissionStatusPayload,
    writeMissionCheckpoint: opts.writeMissionCheckpoint,
  });
}
