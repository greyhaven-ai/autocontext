import { describe, expect, it, vi } from "vitest";

import {
  buildCampaignCreateRequest,
  buildCampaignMissionLinkRequest,
  executeCampaignRouteRequest,
} from "../src/server/campaign-route-workflow.js";

describe("campaign route workflow", () => {
  it("normalizes campaign creation payloads", () => {
    expect(buildCampaignCreateRequest({
      name: 42,
      goal: "Ship OAuth",
      budgetTokens: 100,
      budgetCost: "ignored",
    } as unknown as Record<string, unknown>)).toEqual({
      name: "42",
      goal: "Ship OAuth",
      budgetTokens: 100,
      budgetCost: undefined,
    });
  });

  it("normalizes campaign mission link payloads", () => {
    expect(buildCampaignMissionLinkRequest({
      missionId: 99,
      priority: 3,
      dependsOn: ["m1", 2, "m3"],
    } as unknown as Record<string, unknown>)).toEqual({
      missionId: "99",
      priority: 3,
      dependsOn: ["m1", "m3"],
    });
  });

  it("returns detail and progress responses with 404s for missing campaigns", () => {
    const campaignApi = {
      listCampaigns: vi.fn(),
      createCampaign: vi.fn(),
      getCampaign: vi.fn(() => null),
      getCampaignProgress: vi.fn(() => null),
      addMission: vi.fn(),
      updateStatus: vi.fn(),
    };
    const campaignManager = { budgetUsage: vi.fn() };

    expect(executeCampaignRouteRequest({
      route: "detail",
      campaignId: "camp_1",
      queryStatus: undefined,
      body: {},
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 404,
      body: { error: "Campaign 'camp_1' not found" },
    });

    expect(executeCampaignRouteRequest({
      route: "progress",
      campaignId: "camp_1",
      queryStatus: undefined,
      body: {},
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 404,
      body: { error: "Campaign 'camp_1' not found" },
    });
  });

  it("delegates create, add-mission, and status transitions", () => {
    const campaignApi = {
      listCampaigns: vi.fn(() => [{ id: "camp_1" }]),
      createCampaign: vi.fn(() => ({ id: "camp_2" })),
      getCampaign: vi.fn(() => ({ id: "camp_1" })),
      getCampaignProgress: vi.fn(() => ({ completed: 1, total: 2 })),
      addMission: vi.fn(),
      updateStatus: vi.fn(),
    };
    const campaignManager = {
      budgetUsage: vi.fn(() => ({ totalSteps: 2 })),
    };

    expect(executeCampaignRouteRequest({
      route: "list",
      queryStatus: "active",
      body: {},
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 200,
      body: [{ id: "camp_1" }],
    });

    expect(executeCampaignRouteRequest({
      route: "create",
      body: { name: "Ship OAuth", goal: "Implement login" },
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 200,
      body: { id: "camp_2" },
    });

    expect(executeCampaignRouteRequest({
      route: "add_mission",
      campaignId: "camp_1",
      body: { missionId: "mission_1", priority: 2, dependsOn: ["m0"] },
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 200,
      body: { ok: true },
    });
    expect(campaignApi.addMission).toHaveBeenCalledWith("camp_1", {
      missionId: "mission_1",
      priority: 2,
      dependsOn: ["m0"],
    });

    expect(executeCampaignRouteRequest({
      route: "status",
      campaignId: "camp_1",
      action: "pause",
      body: {},
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 200,
      body: { ok: true, status: "paused" },
    });
    expect(campaignApi.updateStatus).toHaveBeenCalledWith("camp_1", "paused");

    expect(executeCampaignRouteRequest({
      route: "progress",
      campaignId: "camp_1",
      body: {},
      campaignApi,
      campaignManager,
    })).toEqual({
      status: 200,
      body: {
        progress: { completed: 1, total: 2 },
        budget: { totalSteps: 2 },
      },
    });
  });
});
