import { describe, expect, it } from "vitest";

import {
  buildMissingVerifierOutcome,
  resolveMissionVerificationErrorOutcome,
  resolveMissionVerificationOutcome,
} from "../src/mission/verification-workflow.js";

describe("mission verification workflow", () => {
  it("builds a no-verifier outcome without a status transition", () => {
    const outcome = buildMissingVerifierOutcome();

    expect(outcome.result).toEqual({
      passed: false,
      reason: "No verifier registered",
      suggestions: [],
      metadata: {},
    });
    expect(outcome.nextStatus).toBeNull();
  });

  it("maps passing verification results to completed status", () => {
    const outcome = resolveMissionVerificationOutcome({
      passed: true,
      reason: "All checks pass",
      suggestions: [],
      metadata: {},
    });

    expect(outcome.result.passed).toBe(true);
    expect(outcome.nextStatus).toBe("completed");
  });

  it("keeps failing verification results non-terminal", () => {
    const outcome = resolveMissionVerificationOutcome({
      passed: false,
      reason: "Tests still failing",
      suggestions: ["Fix auth"],
      metadata: {},
    });

    expect(outcome.result.passed).toBe(false);
    expect(outcome.nextStatus).toBeNull();
  });

  it("converts verifier exceptions into stable failure outcomes", () => {
    const outcome = resolveMissionVerificationErrorOutcome(
      "Verifier transport failed",
      "Error",
    );

    expect(outcome.result.passed).toBe(false);
    expect(outcome.result.reason).toBe(
      "Verifier error: Verifier transport failed",
    );
    expect(outcome.result.metadata?.verifierThrew).toBe(true);
    expect(outcome.nextStatus).toBeNull();
  });
});
