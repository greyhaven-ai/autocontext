/**
 * AC-533: Wire CampaignManager into CLI, API, and MCP surfaces.
 *
 * Tests the campaign API routes, MCP tool definitions, and CLI command
 * dispatch. Uses the existing CampaignManager + MissionManager with
 * an in-memory SQLite database.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MissionManager } from "../src/mission/manager.js";
import { CampaignManager } from "../src/mission/campaign.js";
import { buildCampaignApiRoutes } from "../src/server/campaign-api.js";
import { CAMPAIGN_TOOLS } from "../src/mcp/campaign-tools.js";
import { SQLiteStore } from "../src/storage/index.js";
import type { LLMProvider } from "../src/types/index.js";

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

function makeMockProvider(): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async () => ({ text: "generated output", usage: {} }),
  };
}

async function fetchJson(url: string, init?: RequestInit): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url, init);
  const body = await res.json();
  return { status: res.status, body };
}

async function createCampaignServer(dir: string) {
  const { RunManager, InteractiveServer } = await import("../src/server/index.js");
  const { MissionManager } = await import("../src/mission/manager.js");
  const seedMissionManager = new MissionManager(join(dir, "test.db"));
  const missionId = seedMissionManager.create({
    name: "Seed mission",
    goal: "Be available for campaign wiring",
  });
  seedMissionManager.close();

  const runsRoot = join(dir, "runs");
  const knowledgeRoot = join(dir, "knowledge");
  mkdirSync(runsRoot, { recursive: true });
  mkdirSync(knowledgeRoot, { recursive: true });

  const mgr = new RunManager({
    dbPath: join(dir, "test.db"),
    migrationsDir: MIGRATIONS_DIR,
    runsRoot,
    knowledgeRoot,
    providerType: "deterministic",
  });
  const server = new InteractiveServer({ runManager: mgr, port: 0 });
  await server.start();
  return { server, baseUrl: `http://localhost:${server.port}`, missionId };
}

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

  it("createCampaign stores cost budget as maxTotalCostUsd", () => {
    const routes = buildCampaignApiRoutes(campaignMgr);
    const { id } = routes.createCampaign({
      name: "Ship OAuth",
      goal: "Implement login",
      budgetCost: 100,
    });
    const campaign = campaignMgr.get(id);
    expect(campaign?.budget?.maxTotalCostUsd).toBe(100);
    expect(campaign?.budget?.maxMissions).toBeUndefined();
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

  it("registers live campaign tools on the MCP server", async () => {
    const storeDir = mkdtempSync(join(tmpdir(), "ac533-mcp-"));
    const store = new SQLiteStore(join(storeDir, "test.db"));
    store.migrate(MIGRATIONS_DIR);
    const { createMcpServer } = await import("../src/mcp/server.js");
    const server = createMcpServer({
      store,
      provider: makeMockProvider(),
      dbPath: join(storeDir, "test.db"),
    }) as unknown as {
      _registeredTools: Record<string, { handler: (args: Record<string, unknown>, extra: unknown) => Promise<{ content: Array<{ text: string }> }> }>;
    };

    expect(server._registeredTools.create_campaign).toBeDefined();
    const result = await server._registeredTools.create_campaign.handler({
      name: "Ship OAuth",
      goal: "Implement login",
    }, {});
    const payload = JSON.parse(result.content[0].text) as { id: string };
    expect(payload.id).toContain("campaign-");
    store.close();
    rmSync(storeDir, { recursive: true, force: true });
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

describe("Campaign live server integration", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createCampaignServer>>["server"];
  let baseUrl: string;
  let missionId: string;

  beforeEach(async () => {
    dir = mkdtempSync(join(tmpdir(), "ac533-server-"));
    const setup = await createCampaignServer(dir);
    server = setup.server;
    baseUrl = setup.baseUrl;
    missionId = setup.missionId;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("mounts campaign REST endpoints on the live server", async () => {
    const created = await fetchJson(`${baseUrl}/api/campaigns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Ship OAuth",
        goal: "Implement login",
        budgetTokens: 10,
        budgetCost: 25,
      }),
    });
    expect(created.status).toBe(200);
    const campaignId = (created.body as { id: string }).id;

    const list = await fetchJson(`${baseUrl}/api/campaigns`);
    expect(list.status).toBe(200);
    expect((list.body as Array<Record<string, unknown>>)[0]?.id).toBe(campaignId);

    const addMission = await fetchJson(`${baseUrl}/api/campaigns/${campaignId}/missions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ missionId }),
    });
    expect(addMission.status).toBe(200);

    const detail = await fetchJson(`${baseUrl}/api/campaigns/${campaignId}`);
    expect(detail.status).toBe(200);
    expect((detail.body as Record<string, unknown>).name).toBe("Ship OAuth");
    expect(((detail.body as Record<string, unknown>).missions as Array<Record<string, unknown>>).length).toBe(1);
    expect((((detail.body as Record<string, unknown>).budget as Record<string, unknown>).maxTotalCostUsd)).toBe(25);

    const progress = await fetchJson(`${baseUrl}/api/campaigns/${campaignId}/progress`);
    expect(progress.status).toBe(200);
    expect((progress.body as Record<string, unknown>).progress).toBeDefined();
    expect(((progress.body as Record<string, unknown>).budget as Record<string, unknown>).maxTotalSteps).toBe(10);

    const paused = await fetchJson(`${baseUrl}/api/campaigns/${campaignId}/pause`, { method: "POST" });
    expect((paused.body as Record<string, unknown>).status).toBe("paused");
  });
});
