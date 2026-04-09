import type { MissionStatus } from "./types.js";

export interface MissionStatusTransition {
  nextStatus: MissionStatus;
  shouldEmitStatusChange: boolean;
}

const ALLOWED_MISSION_STATUS_TRANSITIONS: Record<MissionStatus, readonly MissionStatus[]> = {
  active: [
    "active",
    "paused",
    "completed",
    "failed",
    "canceled",
    "blocked",
    "budget_exhausted",
    "verifier_failed",
  ],
  paused: ["paused", "active", "canceled", "failed"],
  completed: ["completed"],
  failed: ["failed", "active", "canceled"],
  canceled: ["canceled", "active"],
  blocked: ["blocked", "active", "canceled", "failed"],
  budget_exhausted: ["budget_exhausted", "active", "canceled"],
  verifier_failed: ["verifier_failed", "active", "failed", "canceled"],
};

export function canTransitionMissionStatus(
  previousStatus: MissionStatus | undefined,
  nextStatus: MissionStatus,
): boolean {
  if (previousStatus === undefined) {
    return true;
  }

  return ALLOWED_MISSION_STATUS_TRANSITIONS[previousStatus].includes(nextStatus);
}

export function resolveMissionStatusTransition(
  previousStatus: MissionStatus | undefined,
  nextStatus: MissionStatus,
): MissionStatusTransition {
  if (!canTransitionMissionStatus(previousStatus, nextStatus)) {
    throw new Error(
      `Invalid mission status transition: ${previousStatus} -> ${nextStatus}`,
    );
  }

  return {
    nextStatus,
    shouldEmitStatusChange:
      previousStatus !== undefined && previousStatus !== nextStatus,
  };
}
