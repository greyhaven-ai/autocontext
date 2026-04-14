import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createCampaignTables } from "../src/mission/campaign-store-workflow.js";
import {
  createCampaignRecord,
  getCampaignRecord,
  listCampaignRecords,
  touchCampaignRecord,
  updateCampaignStatusRecord,
} from "../src/mission/campaign-store-query-workflow.js";

describe("campaign store query workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-campaign-store-query-"));
    db = new Database(join(dir, "campaign.db"));
    db.pragma("foreign_keys = ON");
    db.exec("CREATE TABLE missions (id TEXT PRIMARY KEY);");
    createCampaignTables(db);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates, reads, lists, touches, and status-updates campaign rows", () => {
    createCampaignRecord(db, "camp-1", {
      name: "Campaign",
      goal: "Goal",
      budget: { maxMissions: 2 },
      metadata: { team: "core" },
    });

    expect(getCampaignRecord(db, "camp-1")).toMatchObject({
      id: "camp-1",
      status: "active",
      budget: { maxMissions: 2 },
      metadata: { team: "core" },
    });
    expect(listCampaignRecords(db)).toHaveLength(1);

    touchCampaignRecord(db, "camp-1");
    expect(getCampaignRecord(db, "camp-1")?.updatedAt).toBeTypeOf("string");

    updateCampaignStatusRecord(db, "camp-1", "completed");
    expect(getCampaignRecord(db, "camp-1")).toMatchObject({
      status: "completed",
      completedAt: expect.any(String),
    });
  });
});
