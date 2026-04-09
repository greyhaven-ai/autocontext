import {
  buildVerifierErrorResult,
  deriveMissionStatusFromVerifierResult,
} from "./lifecycle.js";
import type { MissionStatus, VerifierResult } from "./types.js";

export interface MissionVerificationOutcome {
  result: VerifierResult;
  nextStatus: MissionStatus | null;
}

export function buildMissingVerifierOutcome(): MissionVerificationOutcome {
  return {
    result: {
      passed: false,
      reason: "No verifier registered",
      suggestions: [],
      metadata: {},
    },
    nextStatus: null,
  };
}

export function resolveMissionVerificationOutcome(
  result: VerifierResult,
): MissionVerificationOutcome {
  return {
    result,
    nextStatus: deriveMissionStatusFromVerifierResult(result),
  };
}

export function resolveMissionVerificationErrorOutcome(
  message: string,
  errorName: string,
): MissionVerificationOutcome {
  const result = buildVerifierErrorResult(message, errorName);
  return {
    result,
    nextStatus: null,
  };
}
