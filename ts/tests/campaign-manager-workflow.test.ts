import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  collectCampaignMissionSnapshots,
  reconcileCampaignRecord,
  requireCampaign,
  validateCampaignMissionAddition,
} from "../src/mission/campaign-manager-workflow.js";
import {
  countCampaignMissions,
  hasCampaignMission,
  insertCampaignMissionRecord,
  listCampaignMissionEntries,
  removeCampaignMissionRecord,
} from "../src/mission/campaign-membership-store-workflow.js";
import { createCampaignTables } from "../src/mission/campaign-store-workflow.js";

describe("campaign manager workflows", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-campaign-workflow-"));
    db = new Database(join(dir, "campaign.db"));
    db.pragma("foreign_keys = ON");
    db.exec("CREATE TABLE missions (id TEXT PRIMARY KEY);");
    createCampaignTables(db);
    db.prepare(
      `INSERT INTO campaigns (id, name, goal, status, budget, metadata, created_at)
       VALUES ('camp-1', 'Campaign', 'Goal', 'active', NULL, '{}', '2026-01-01T00:00:00Z')`,
    ).run();
    db.prepare("INSERT INTO missions (id) VALUES ('mission-1'), ('mission-2')").run();
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("validates mission membership rules and persists membership ordering", () => {
    expect(() => requireCampaign(null, "camp-missing")).toThrow("Campaign not found: camp-missing");
    validateCampaignMissionAddition({
      campaignId: "camp-1",
      missionId: "mission-1",
      missionExists: true,
      missionAlreadyLinked: false,
      dependsOn: [],
      hasMissionInCampaign: () => false,
    });

    insertCampaignMissionRecord(db, "camp-1", "mission-1", { priority: 2 }, countCampaignMissions(db, "camp-1"));
    expect(hasCampaignMission(db, "camp-1", "mission-1")).toBe(true);
    expect(listCampaignMissionEntries(db, "camp-1")).toEqual([
      expect.objectContaining({ missionId: "mission-1", priority: 2 }),
    ]);

    expect(() =>
      validateCampaignMissionAddition({
        campaignId: "camp-1",
        missionId: "mission-1",
        missionExists: true,
        missionAlreadyLinked: true,
        dependsOn: [],
        hasMissionInCampaign: () => true,
      }),
    ).toThrow("Mission already in campaign: mission-1");

    expect(() =>
      validateCampaignMissionAddition({
        campaignId: "camp-1",
        missionId: "mission-2",
        missionExists: true,
        missionAlreadyLinked: false,
        dependsOn: ["missing"],
        hasMissionInCampaign: () => false,
      }),
    ).toThrow("Dependency mission not in campaign: missing");

    removeCampaignMissionRecord(db, "camp-1", "mission-1");
    expect(countCampaignMissions(db, "camp-1")).toBe(0);
  });

  it("collects mission snapshots and reconciles campaign status transitions", () => {
    insertCampaignMissionRecord(db, "camp-1", "mission-1", { priority: 1 }, 0);
    insertCampaignMissionRecord(db, "camp-1", "mission-2", { priority: 2 }, 1);
    const store = {
      getCampaign: (id: string) =>
        id === "camp-1"
          ? {
              id: "camp-1",
              name: "Campaign",
              goal: "Goal",
              status: "active" as const,
              metadata: {},
              createdAt: "2026-01-01T00:00:00Z",
            }
          : null,
      missions: (id: string) => listCampaignMissionEntries(db, id),
      hasMission: (campaignId: string, missionId: string) => hasCampaignMission(db, campaignId, missionId),
      setStatus: (_campaignId: string, _status: "active" | "paused" | "completed" | "failed" | "canceled") => undefined,
    };
    const missionManager = {
      get: (missionId: string) =>
        missionId === "mission-1"
          ? { status: "completed" }
          : missionId === "mission-2"
            ? { status: "failed" }
            : null,
      steps: (missionId: string) => (missionId === "mission-1" ? [1, 2] : [1]),
    };

    const entries = listCampaignMissionEntries(db, "camp-1");
    expect(collectCampaignMissionSnapshots(entries, missionManager)).toEqual([
      { status: "completed", stepCount: 2 },
      { status: "failed", stepCount: 1 },
    ]);

    const setStatusCalls: string[] = [];
    const reconciled = reconcileCampaignRecord(
      "camp-1",
      { ...store, setStatus: (_id, status) => setStatusCalls.push(status) },
      missionManager,
    );

    expect(setStatusCalls).toEqual(["failed"]);
    expect(reconciled).toMatchObject({ id: "camp-1", status: "active" });
  });
});
