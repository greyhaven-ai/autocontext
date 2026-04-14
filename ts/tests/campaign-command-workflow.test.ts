import { describe, expect, it } from "vitest";

import {
  buildCampaignAddMissionResult,
  buildCampaignProgressPayload,
  buildCampaignStatusDetail,
  CAMPAIGN_HELP_TEXT,
  getCampaignIdOrThrow,
  parseCampaignStatus,
  planCampaignAddMission,
  planCampaignCreate,
  validateCampaignLifecycleAction,
} from "../src/cli/campaign-command-workflow.js";

describe("campaign command workflow", () => {
  it("exposes stable help text", () => {
    expect(CAMPAIGN_HELP_TEXT).toContain("autoctx campaign");
    expect(CAMPAIGN_HELP_TEXT).toContain("create");
    expect(CAMPAIGN_HELP_TEXT).toContain("add-mission");
    expect(CAMPAIGN_HELP_TEXT).toContain("progress");
    expect(CAMPAIGN_HELP_TEXT.toLowerCase()).toContain("see also");
  });

  it("plans campaign creation with optional budget", () => {
    expect(
      planCampaignCreate(
        {
          name: "Budgeted",
          goal: "Ship OAuth and billing",
          "max-missions": "5",
          "max-steps": "50",
        },
        (raw: string | undefined, _label: string) => Number(raw),
      ),
    ).toEqual({
      name: "Budgeted",
      goal: "Ship OAuth and billing",
      budget: {
        maxMissions: 5,
        maxTotalSteps: 50,
      },
    });
  });

  it("requires campaign name and goal", () => {
    expect(() =>
      planCampaignCreate(
        {
          name: undefined,
          goal: undefined,
          "max-missions": undefined,
          "max-steps": undefined,
        },
        (raw: string | undefined, _label: string) => Number(raw),
      ),
    ).toThrow(
      "Usage: autoctx campaign create --name <name> --goal <goal> [--max-missions N] [--max-steps N]",
    );
  });

  it("parses campaign status filters", () => {
    expect(parseCampaignStatus(undefined)).toBeUndefined();
    expect(parseCampaignStatus("active")).toBe("active");
    expect(() => parseCampaignStatus("mystery")).toThrow(
      "Error: --status must be one of active, paused, completed, failed, canceled",
    );
  });

  it("requires campaign ids for id-based actions", () => {
    expect(() =>
      getCampaignIdOrThrow({}, "Usage: autoctx campaign status --id <campaign-id>"),
    ).toThrow("Usage: autoctx campaign status --id <campaign-id>");
    expect(
      getCampaignIdOrThrow(
        { id: "campaign-1" },
        "Usage: autoctx campaign status --id <campaign-id>",
      ),
    ).toBe("campaign-1");
  });

  it("plans add-mission requests", () => {
    expect(
      planCampaignAddMission(
        {
          id: "campaign-1",
          "mission-id": "mission-1",
          priority: "10",
          "depends-on": "mission-0",
        },
        (raw: string | undefined, _label: string) => Number(raw),
      ),
    ).toEqual({
      campaignId: "campaign-1",
      missionId: "mission-1",
      options: {
        priority: 10,
        dependsOn: ["mission-0"],
      },
    });
  });

  it("requires campaign and mission ids for add-mission", () => {
    expect(() =>
      planCampaignAddMission(
        {
          id: undefined,
          "mission-id": undefined,
          priority: undefined,
          "depends-on": undefined,
        },
        (raw: string | undefined, _label: string) => Number(raw),
      ),
    ).toThrow(
      "Usage: autoctx campaign add-mission --id <campaign-id> --mission-id <mission-id> [--priority N] [--depends-on <id>]",
    );
  });

  it("validates lifecycle action guardrails", () => {
    expect(() => validateCampaignLifecycleAction("pause", "paused")).toThrow(
      "Cannot pause campaign in status: paused",
    );
    expect(() => validateCampaignLifecycleAction("resume", "canceled")).toThrow(
      "Cannot resume campaign in status: canceled",
    );
    expect(() => validateCampaignLifecycleAction("cancel", "completed")).toThrow(
      "Cannot cancel campaign in status: completed",
    );
    expect(() => validateCampaignLifecycleAction("pause", "active")).not.toThrow();
    expect(() => validateCampaignLifecycleAction("resume", "paused")).not.toThrow();
    expect(() => validateCampaignLifecycleAction("cancel", "active")).not.toThrow();
  });

  it("builds campaign detail and progress payloads", () => {
    expect(
      buildCampaignStatusDetail(
        { id: "campaign-1", status: "active", name: "Campaign" },
        { totalMissions: 2 },
        [{ missionId: "mission-1" }],
      ),
    ).toEqual({
      id: "campaign-1",
      status: "active",
      name: "Campaign",
      progress: { totalMissions: 2 },
      missions: [{ missionId: "mission-1" }],
    });

    expect(
      buildCampaignProgressPayload(
        { totalMissions: 2, completedMissions: 1 },
        { missionsUsed: 2, exhausted: false },
      ),
    ).toEqual({
      totalMissions: 2,
      completedMissions: 1,
      budgetUsage: { missionsUsed: 2, exhausted: false },
    });
  });

  it("builds add-mission success payloads", () => {
    expect(buildCampaignAddMissionResult("campaign-1", "mission-1")).toEqual({
      ok: true,
      campaignId: "campaign-1",
      missionId: "mission-1",
    });
  });
});
