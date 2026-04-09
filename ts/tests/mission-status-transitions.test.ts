import { describe, expect, it } from "vitest";

import {
  canTransitionMissionStatus,
  resolveMissionStatusTransition,
} from "../src/mission/status-transitions.js";

describe("mission status transitions", () => {
  it("allows the mission workflow transitions used by the control plane", () => {
    expect(canTransitionMissionStatus("active", "paused")).toBe(true);
    expect(canTransitionMissionStatus("active", "blocked")).toBe(true);
    expect(canTransitionMissionStatus("active", "budget_exhausted")).toBe(true);
    expect(canTransitionMissionStatus("active", "verifier_failed")).toBe(true);
    expect(canTransitionMissionStatus("active", "completed")).toBe(true);
    expect(canTransitionMissionStatus("canceled", "active")).toBe(true);
    expect(canTransitionMissionStatus("blocked", "active")).toBe(true);
    expect(canTransitionMissionStatus("verifier_failed", "active")).toBe(true);
  });

  it("treats same-status writes as valid no-ops", () => {
    expect(resolveMissionStatusTransition("active", "active")).toEqual({
      nextStatus: "active",
      shouldEmitStatusChange: false,
    });
  });

  it("rejects unsupported transitions", () => {
    expect(canTransitionMissionStatus("completed", "paused")).toBe(false);
    expect(() => resolveMissionStatusTransition("completed", "paused")).toThrow(
      "Invalid mission status transition: completed -> paused",
    );
  });
});
