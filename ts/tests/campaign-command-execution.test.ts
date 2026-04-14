import { describe, expect, it, vi } from "vitest";

import {
  executeCampaignAddMissionCommand,
  executeCampaignCreateCommand,
  executeCampaignLifecycleCommand,
  executeCampaignListCommand,
  executeCampaignProgressCommand,
  executeCampaignStatusCommand,
} from "../src/cli/campaign-command-execution.js";

describe("campaign command execution", () => {
  it("creates campaigns from planned input", () => {
    const create = vi.fn(() => "campaign-1");
    const get = vi.fn(() => ({ id: "campaign-1", status: "active" }));

    expect(
      executeCampaignCreateCommand({
        manager: { create, get },
        plan: {
          name: "Budgeted",
          goal: "Ship OAuth",
          budget: { maxMissions: 5 },
        },
      }),
    ).toEqual({ id: "campaign-1", status: "active" });

    expect(create).toHaveBeenCalledWith({
      name: "Budgeted",
      goal: "Ship OAuth",
      budget: { maxMissions: 5 },
    });
    expect(get).toHaveBeenCalledWith("campaign-1");
  });

  it("returns detailed campaign status with progress and missions", () => {
    expect(
      executeCampaignStatusCommand({
        campaignId: "campaign-1",
        getCampaign: () => ({ id: "campaign-1", status: "active" }),
        getProgress: () => ({ totalMissions: 2 }),
        getMissions: () => [{ missionId: "mission-1" }],
      }),
    ).toEqual({
      id: "campaign-1",
      status: "active",
      progress: { totalMissions: 2 },
      missions: [{ missionId: "mission-1" }],
    });
  });

  it("lists campaigns with optional status filter", () => {
    const list = vi.fn(() => [{ id: "campaign-1" }]);
    expect(
      executeCampaignListCommand({
        listCampaigns: list,
        status: "active",
      }),
    ).toEqual([{ id: "campaign-1" }]);
    expect(list).toHaveBeenCalledWith("active");
  });

  it("adds missions to campaigns and returns success payloads", () => {
    const addMission = vi.fn();
    expect(
      executeCampaignAddMissionCommand({
        addMission,
        plan: {
          campaignId: "campaign-1",
          missionId: "mission-1",
          options: { priority: 10, dependsOn: ["mission-0"] },
        },
      }),
    ).toEqual({
      ok: true,
      campaignId: "campaign-1",
      missionId: "mission-1",
    });
    expect(addMission).toHaveBeenCalledWith("campaign-1", "mission-1", {
      priority: 10,
      dependsOn: ["mission-0"],
    });
  });

  it("returns campaign progress with budget usage", () => {
    expect(
      executeCampaignProgressCommand({
        campaignId: "campaign-1",
        getProgress: () => ({ totalMissions: 2 }),
        getBudgetUsage: () => ({ missionsUsed: 1, exhausted: false }),
      }),
    ).toEqual({
      totalMissions: 2,
      budgetUsage: { missionsUsed: 1, exhausted: false },
    });
  });

  it("applies lifecycle actions after validating campaign state", () => {
    const pause = vi.fn();
    const getCampaign = vi
      .fn()
      .mockReturnValueOnce({ id: "campaign-1", status: "active" })
      .mockReturnValueOnce({ id: "campaign-1", status: "paused" });

    expect(
      executeCampaignLifecycleCommand({
        action: "pause",
        campaignId: "campaign-1",
        manager: { pause, get: getCampaign },
      }),
    ).toEqual({ id: "campaign-1", status: "paused" });

    expect(pause).toHaveBeenCalledWith("campaign-1");
  });
});
