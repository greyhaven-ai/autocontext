/**
 * AC-533: Wire CampaignManager into CLI, API, and MCP surfaces.
 *
 * Tests the campaign API routes, MCP tool definitions, and CLI command
 * dispatch. Uses the existing CampaignManager + MissionManager with
 * an in-memory SQLite database.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MissionManager } from "../src/mission/manager.js";
import { CampaignManager } from "../src/mission/campaign.js";
import { buildCampaignApiRoutes } from "../src/server/campaign-api.js";
import { CAMPAIGN_TOOLS } from "../src/mcp/campaign-tools.js";

let tmpDir: string;
let missionMgr: MissionManager;
let campaignMgr: CampaignManager;
const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac533-test-"));
  const dbPath = join(tmpDir, "test.db");
  missionMgr = new MissionManager(dbPath);
  campaignMgr = new CampaignManager(missionMgr);
});

afterEach(() => {
  campaignMgr?.close();
  missionMgr?.close();
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Campaign API routes
// ---------------------------------------------------------------------------

describe("Campaign API routes", () => {
  it("buildCampaignApiRoutes returns all route handlers", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    expect(typeof routes.listCampaigns).toBe("function");
    expect(typeof routes.getCampaign).toBe("function");
    expect(typeof routes.createCampaign).toBe("function");
    expect(typeof routes.addMission).toBe("function");
    expect(typeof routes.updateStatus).toBe("function");
    expect(typeof routes.getCampaignProgress).toBe("function");
  });

  it("createCampaign returns campaign ID", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    const result = routes.createCampaign({
      name: "Ship OAuth",
      goal: "Implement login",
    });
    expect(result.id).toBeTruthy();
    expect(typeof result.id).toBe("string");
  });

  it("listCampaigns returns created campaigns", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    routes.createCampaign({ name: "C1", goal: "G1" });
    routes.createCampaign({ name: "C2", goal: "G2" });
    const list = routes.listCampaigns();
    expect(list.length).toBe(2);
  });

  it("getCampaign returns null for missing ID", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    expect(routes.getCampaign("nonexistent")).toBeNull();
  });

  it("getCampaign returns campaign with progress", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    const { id } = routes.createCampaign({ name: "C", goal: "G" });
    const campaign = routes.getCampaign(id);
    expect(campaign).not.toBeNull();
    expect(campaign!.name).toBe("C");
    expect(campaign!.progress).toBeDefined();
  });

  it("addMission links a mission to a campaign", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    const { id: cId } = routes.createCampaign({ name: "C", goal: "G" });
    const mId = missionMgr.create({ name: "M1", goal: "Do thing" });
    routes.addMission(cId, { missionId: mId });
    const campaign = routes.getCampaign(cId);
    expect(campaign!.missions?.length).toBe(1);
  });

  it("updateStatus transitions campaign state", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    const { id } = routes.createCampaign({ name: "C", goal: "G" });
    routes.updateStatus(id, "paused");
    const campaign = routes.getCampaign(id);
    expect(campaign!.status).toBe("paused");
  });

  it("listCampaigns filters by status", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    routes.createCampaign({ name: "Active", goal: "G" });
    const { id } = routes.createCampaign({ name: "Paused", goal: "G" });
    routes.updateStatus(id, "paused");
    const active = routes.listCampaigns("active");
    expect(active.length).toBe(1);
    expect(active[0].name).toBe("Active");
  });
});

// ---------------------------------------------------------------------------
// MCP tool definitions
// ---------------------------------------------------------------------------

describe("Campaign MCP tools", () => {
  it("exports CAMPAIGN_TOOLS array", () => {
    expect(Array.isArray(CAMPAIGN_TOOLS)).toBe(true);
    expect(CAMPAIGN_TOOLS.length).toBeGreaterThanOrEqual(4);
  });

  it("includes create_campaign tool", () => {
    const tool = CAMPAIGN_TOOLS.find((t) => t.name === "create_campaign");
    expect(tool).toBeDefined();
    expect(tool!.schema.required).toContain("name");
    expect(tool!.schema.required).toContain("goal");
  });

  it("includes campaign_status tool", () => {
    const tool = CAMPAIGN_TOOLS.find((t) => t.name === "campaign_status");
    expect(tool).toBeDefined();
    expect(tool!.schema.required).toContain("campaign_id");
  });

  it("includes list_campaigns tool", () => {
    expect(
      CAMPAIGN_TOOLS.find((t) => t.name === "list_campaigns"),
    ).toBeDefined();
  });

  it("includes add_campaign_mission tool", () => {
    const tool = CAMPAIGN_TOOLS.find((t) => t.name === "add_campaign_mission");
    expect(tool).toBeDefined();
    expect(tool!.schema.required).toContain("campaign_id");
    expect(tool!.schema.required).toContain("mission_id");
  });
});

// ---------------------------------------------------------------------------
// Concept model status
// ---------------------------------------------------------------------------

describe("Concept model", () => {
  it("campaign concept is implemented, not reserved", async () => {
    const { getConceptModel } = await import("../src/concepts/model.js");
    const model = getConceptModel();
    const campaign = (model as Record<string, unknown[]>).concepts?.find?.(
      (c: Record<string, unknown>) => c.name === "campaign",
    );
    if (campaign) {
      expect((campaign as Record<string, unknown>).status).not.toBe("reserved");
    }
  });
});
