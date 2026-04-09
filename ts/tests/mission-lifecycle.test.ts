import { describe, expect, it } from "vitest";

import {
  buildVerifierErrorResult,
  deriveMissionStatusFromVerifierResult,
  resolveMissionStatusTransition,
} from "../src/mission/lifecycle.js";

describe("mission lifecycle helpers", () => {
  it("emits status changes only when the status actually changes", () => {
    expect(resolveMissionStatusTransition("active", "paused")).toEqual({
      nextStatus: "paused",
      shouldEmitStatusChange: true,
    });
    expect(resolveMissionStatusTransition("active", "active")).toEqual({
      nextStatus: "active",
      shouldEmitStatusChange: false,
    });
  });

  it("maps successful verifier results to completed status", () => {
    expect(
      deriveMissionStatusFromVerifierResult({
        passed: true,
        reason: "done",
        suggestions: [],
        metadata: {},
      }),
    ).toBe("completed");

    expect(
      deriveMissionStatusFromVerifierResult({
        passed: false,
        reason: "not done",
        suggestions: [],
        metadata: {},
      }),
    ).toBeNull();
  });

  it("builds verifier error results consistently", () => {
    expect(buildVerifierErrorResult("boom", "TypeError")).toEqual({
      passed: false,
      reason: "Verifier error: boom",
      suggestions: [],
      metadata: {
        verifierThrew: true,
        errorName: "TypeError",
      },
    });
  });
});
