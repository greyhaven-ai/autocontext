import { describe, expect, it } from "vitest";

import type {
  Campaign,
  CampaignMissionEntry,
  CampaignStatus,
} from "../src/mission/campaign-contracts.js";
import {
  buildCampaignBudgetUsageReport,
  buildCampaignProgressReport,
  getCampaignWithReconciledStatus,
  listCampaignsWithReconciledStatus,
  setCampaignLifecycleStatus,
} from "../src/mission/campaign-manager-access-workflow.js";

function makeCampaign(status: CampaignStatus = "active", id = "campaign-1"): Campaign {
  return {
    id,
    name: `Campaign ${id}`,
    goal: "Goal",
    status,
    metadata: {},
    createdAt: "2026-01-01T00:00:00Z",
    budget: { maxMissions: 3, maxTotalSteps: 10 },
  };
}

function makeEntry(missionId: string, priority: number): CampaignMissionEntry {
  return {
    campaignId: "campaign-1",
    missionId,
    priority,
    dependsOn: [],
    addedAt: `2026-01-01T00:00:0${priority}Z`,
  };
}

describe("campaign manager access workflow", () => {
  it("reconciles campaign get and list views before returning records", () => {
    const campaigns = new Map<string, Campaign>([
      ["campaign-1", makeCampaign("active", "campaign-1")],
      ["campaign-2", makeCampaign("paused", "campaign-2")],
    ]);
    const missionEntries = new Map<string, CampaignMissionEntry[]>([
      ["campaign-1", [makeEntry("mission-1", 1)]],
      ["campaign-2", [makeEntry("mission-2", 1)]],
    ]);
    const missionManager = {
      get: (missionId: string) =>
        missionId === "mission-1"
          ? { status: "completed" }
          : missionId === "mission-2"
            ? { status: "active" }
            : null,
      steps: () => [1],
    };
    const store = {
      getCampaign: (campaignId: string) => campaigns.get(campaignId) ?? null,
      listCampaigns: () => Array.from(campaigns.values()),
      missions: (campaignId: string) => missionEntries.get(campaignId) ?? [],
      hasMission: (campaignId: string, missionId: string) =>
        (missionEntries.get(campaignId) ?? []).some((entry) => entry.missionId === missionId),
      setStatus: (campaignId: string, status: CampaignStatus) => {
        const campaign = campaigns.get(campaignId);
        if (campaign) {
          campaigns.set(campaignId, { ...campaign, status });
        }
      },
    };

    expect(getCampaignWithReconciledStatus("campaign-1", store, missionManager)).toMatchObject({
      id: "campaign-1",
      status: "completed",
    });

    expect(listCampaignsWithReconciledStatus("paused", store, missionManager)).toEqual([
      expect.objectContaining({ id: "campaign-2", status: "paused" }),
    ]);
  });

  it("builds campaign progress and budget usage from reconciled mission snapshots", () => {
    const store = {
      getCampaign: (campaignId: string) =>
        campaignId === "campaign-1" ? makeCampaign("active") : null,
      missions: () => [makeEntry("mission-1", 1), makeEntry("mission-2", 2)],
      hasMission: () => true,
      setStatus: () => undefined,
    };
    const missionManager = {
      get: (missionId: string) =>
        missionId === "mission-1"
          ? { status: "completed" }
          : missionId === "mission-2"
            ? { status: "failed" }
            : null,
      steps: (missionId: string) => (missionId === "mission-1" ? [1, 2, 3] : [1]),
    };

    expect(buildCampaignProgressReport("campaign-1", store, missionManager)).toEqual({
      totalMissions: 2,
      completedMissions: 1,
      failedMissions: 1,
      activeMissions: 0,
      totalSteps: 4,
      percentComplete: 50,
      allMissionsComplete: false,
    });

    expect(buildCampaignBudgetUsageReport("campaign-1", store, missionManager)).toEqual({
      missionsUsed: 2,
      maxMissions: 3,
      totalStepsUsed: 4,
      maxTotalSteps: 10,
      exhausted: false,
    });
  });

  it("enforces lifecycle transitions when setting campaign status", () => {
    let campaign = makeCampaign("active");
    const store = {
      getCampaign: () => campaign,
      missions: () => [],
      hasMission: () => false,
      setStatus: (_campaignId: string, status: CampaignStatus) => {
        campaign = { ...campaign, status };
      },
    };

    setCampaignLifecycleStatus("campaign-1", "paused", store);
    expect(campaign.status).toBe("paused");

    setCampaignLifecycleStatus("campaign-1", "canceled", store);
    expect(campaign.status).toBe("canceled");

    expect(() => setCampaignLifecycleStatus("campaign-1", "paused", store)).toThrow(
      "Cannot pause campaign in status: canceled",
    );
  });
});
