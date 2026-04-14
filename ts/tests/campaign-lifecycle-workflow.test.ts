import { describe, expect, it } from "vitest";

import {
  assertLifecycleTransitionAllowed,
  buildCampaignBudgetUsage,
  buildCampaignProgress,
  deriveReconciledCampaignStatus,
} from "../src/mission/campaign-lifecycle-workflow.js";

describe("campaign lifecycle workflow", () => {
  it("builds campaign progress and budget usage from mission snapshots", () => {
    const entries = [
      { campaignId: "c1", missionId: "m1", priority: 1, dependsOn: [], addedAt: "t1" },
      { campaignId: "c1", missionId: "m2", priority: 2, dependsOn: ["m1"], addedAt: "t2" },
    ];
    const snapshots = [
      { status: "completed", stepCount: 2 },
      { status: "active", stepCount: 3 },
    ] as const;

    expect(buildCampaignProgress(entries, [...snapshots])).toEqual({
      totalMissions: 2,
      completedMissions: 1,
      failedMissions: 0,
      activeMissions: 1,
      totalSteps: 5,
      percentComplete: 50,
      allMissionsComplete: false,
    });

    expect(
      buildCampaignBudgetUsage(
        { id: "c1", name: "C", goal: "G", status: "active", budget: { maxMissions: 2, maxTotalSteps: 5 }, metadata: {}, createdAt: "now" },
        entries,
        5,
      ),
    ).toEqual({
      missionsUsed: 2,
      maxMissions: 2,
      totalStepsUsed: 5,
      maxTotalSteps: 5,
      exhausted: true,
    });
  });

  it("derives reconciled campaign statuses and rejects invalid lifecycle transitions", () => {
    const entries = [{ campaignId: "c1", missionId: "m1", priority: 1, dependsOn: [], addedAt: "t1" }];

    expect(
      deriveReconciledCampaignStatus(
        { id: "c1", name: "C", goal: "G", status: "active", metadata: {}, createdAt: "now" },
        entries,
        [{ status: "completed", stepCount: 1 }],
      ),
    ).toBe("completed");
    expect(
      deriveReconciledCampaignStatus(
        { id: "c1", name: "C", goal: "G", status: "active", metadata: {}, createdAt: "now" },
        entries,
        [{ status: "failed", stepCount: 1 }],
      ),
    ).toBe("failed");
    expect(
      deriveReconciledCampaignStatus(
        { id: "c1", name: "C", goal: "G", status: "paused", metadata: {}, createdAt: "now" },
        [],
        [],
      ),
    ).toBe("paused");

    expect(() => assertLifecycleTransitionAllowed("canceled", "active")).toThrow(
      "Cannot resume campaign in status: canceled",
    );
  });
});
