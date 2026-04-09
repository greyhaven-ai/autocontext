import type { MissionStatus, VerifierResult } from "./types.js";

export interface MissionStatusTransition {
  nextStatus: MissionStatus;
  shouldEmitStatusChange: boolean;
}

export function resolveMissionStatusTransition(
  previousStatus: MissionStatus | undefined,
  nextStatus: MissionStatus,
): MissionStatusTransition {
  return {
    nextStatus,
    shouldEmitStatusChange: previousStatus !== undefined && previousStatus !== nextStatus,
  };
}

export function deriveMissionStatusFromVerifierResult(
  result: VerifierResult,
): MissionStatus | null {
  return result.passed ? "completed" : null;
}

export function buildVerifierErrorResult(
  message: string,
  errorName: string,
): VerifierResult {
  return {
    passed: false,
    reason: `Verifier error: ${message}`,
    suggestions: [],
    metadata: {
      verifierThrew: true,
      errorName,
    },
  };
}
