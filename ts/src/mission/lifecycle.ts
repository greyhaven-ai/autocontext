import type { MissionStatus, VerifierResult } from "./types.js";

export {
  canTransitionMissionStatus,
  resolveMissionStatusTransition,
  type MissionStatusTransition,
} from "./status-transitions.js";

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
