import { MissionProgressMsgSchema, type ServerMessage } from "./protocol.js";
import type {
  MissionCreatedEvent,
  MissionStatusChangedEvent,
  MissionStepEvent,
  MissionVerifiedEvent,
} from "../mission/events.js";

export interface MissionProgressEvents {
  on(event: "mission_created", listener: (event: MissionCreatedEvent) => void): void;
  on(event: "mission_step", listener: (event: MissionStepEvent) => void): void;
  on(event: "mission_status_changed", listener: (event: MissionStatusChangedEvent) => void): void;
  on(event: "mission_verified", listener: (event: MissionVerifiedEvent) => void): void;
  off(event: "mission_created", listener: (event: MissionCreatedEvent) => void): void;
  off(event: "mission_step", listener: (event: MissionStepEvent) => void): void;
  off(event: "mission_status_changed", listener: (event: MissionStatusChangedEvent) => void): void;
  off(event: "mission_verified", listener: (event: MissionVerifiedEvent) => void): void;
}

export interface MissionProgressManager {
  get(missionId: string): { status: string } | null | undefined;
  steps(missionId: string): Array<{ description?: string }>;
  budgetUsage(missionId: string): { stepsUsed: number; maxSteps?: number };
}

export interface MissionProgressMessageOpts {
  missionId: string;
  latestStep?: string;
  missionManager: MissionProgressManager;
}

export type MissionProgressMessage = Extract<ServerMessage, { type: "mission_progress" }>;

export function buildMissionProgressMessage(
  opts: MissionProgressMessageOpts,
): MissionProgressMessage | null {
  const mission = opts.missionManager.get(opts.missionId);
  if (!mission) {
    return null;
  }

  const steps = opts.missionManager.steps(opts.missionId);
  const budget = opts.missionManager.budgetUsage(opts.missionId);
  return MissionProgressMsgSchema.parse({
    type: "mission_progress",
    missionId: opts.missionId,
    status: mission.status,
    stepsCompleted: steps.length,
    latestStep: opts.latestStep ?? steps.at(-1)?.description,
    budgetUsed: budget.stepsUsed,
    budgetMax: budget.maxSteps,
  });
}

export function subscribeToMissionProgressEvents(opts: {
  missionEvents: MissionProgressEvents;
  buildMissionProgress: (missionId: string, latestStep?: string) => MissionProgressMessage | null;
  onProgress: (message: MissionProgressMessage) => void;
}): () => void {
  const emitProgress = (missionId: string, latestStep?: string) => {
    const message = opts.buildMissionProgress(missionId, latestStep);
    if (message) {
      opts.onProgress(message);
    }
  };

  const onMissionCreated = (event: MissionCreatedEvent) => emitProgress(event.missionId);
  const onMissionStep = (event: MissionStepEvent) => emitProgress(event.missionId, event.description);
  const onMissionStatusChanged = (event: MissionStatusChangedEvent) => emitProgress(event.missionId);
  const onMissionVerified = (event: MissionVerifiedEvent) => emitProgress(event.missionId);

  opts.missionEvents.on("mission_created", onMissionCreated);
  opts.missionEvents.on("mission_step", onMissionStep);
  opts.missionEvents.on("mission_status_changed", onMissionStatusChanged);
  opts.missionEvents.on("mission_verified", onMissionVerified);

  return () => {
    opts.missionEvents.off("mission_created", onMissionCreated);
    opts.missionEvents.off("mission_step", onMissionStep);
    opts.missionEvents.off("mission_status_changed", onMissionStatusChanged);
    opts.missionEvents.off("mission_verified", onMissionVerified);
  };
}
