import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CampaignStore } from "../src/mission/campaign-store.js";

describe("CampaignStore", () => {
  let dir: string;
  let dbPath: string;
  let seedDb: Database.Database;
  let store: CampaignStore;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-campaign-store-"));
    dbPath = join(dir, "campaign.db");
    seedDb = new Database(dbPath);
    seedDb.pragma("foreign_keys = ON");
    seedDb.exec("CREATE TABLE missions (id TEXT PRIMARY KEY);");
    seedDb.exec("INSERT INTO missions (id) VALUES ('mission-1'), ('mission-2');");
    seedDb.close();
    store = new CampaignStore(dbPath);
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("persists campaigns and mission membership through the internal facade", () => {
    const campaignId = store.createCampaign({
      name: "Campaign",
      goal: "Goal",
      budget: { maxMissions: 2 },
      metadata: { source: "test" },
    });

    expect(store.getCampaign(campaignId)).toMatchObject({
      id: campaignId,
      status: "active",
      budget: { maxMissions: 2 },
      metadata: { source: "test" },
    });
    expect(store.listCampaigns()).toHaveLength(1);

    store.addMission(campaignId, "mission-1", { priority: 1 });
    store.addMission(campaignId, "mission-2", { dependsOn: ["mission-1"] });

    expect(store.missionCount(campaignId)).toBe(2);
    expect(store.hasMission(campaignId, "mission-2")).toBe(true);
    expect(store.missions(campaignId)).toEqual([
      expect.objectContaining({ missionId: "mission-1", priority: 1, dependsOn: [] }),
      expect.objectContaining({ missionId: "mission-2", priority: 2, dependsOn: ["mission-1"] }),
    ]);

    store.setStatus(campaignId, "completed");
    expect(store.getCampaign(campaignId)).toMatchObject({
      status: "completed",
      completedAt: expect.any(String),
    });

    store.removeMission(campaignId, "mission-1");
    expect(store.missionCount(campaignId)).toBe(1);
  });
});
