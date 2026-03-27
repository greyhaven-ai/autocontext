/**
 * AC-428: Campaign abstraction — coordinating multiple missions.
 *
 * Tests verify that campaigns model long-term goals above missions,
 * with lifecycle management, budget tracking, progress aggregation,
 * and campaign-level verification.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  CampaignManager,
  type Campaign,
  type CampaignProgress,
} from "../src/mission/campaign.js";
import { MissionManager } from "../src/mission/manager.js";

let tmpDir: string;
let dbPath: string;
let missionManager: MissionManager;
let campaignManager: CampaignManager;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-428-test-"));
  dbPath = join(tmpDir, "missions.db");
  missionManager = new MissionManager(dbPath);
  campaignManager = new CampaignManager(missionManager);
});
afterEach(() => {
  campaignManager?.close();
  missionManager?.close();
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Campaign lifecycle
// ---------------------------------------------------------------------------

describe("campaign lifecycle", () => {
  it("creates a campaign with name, goal, and optional budget", () => {
    const id = campaignManager.create({
      name: "Ship OAuth",
      goal: "Implement complete OAuth login across all services",
      budget: { maxMissions: 5, maxTotalSteps: 50 },
    });

    expect(id).toBeTruthy();
    const campaign = campaignManager.get(id);
    expect(campaign).not.toBeNull();
    expect(campaign!.name).toBe("Ship OAuth");
    expect(campaign!.goal).toContain("OAuth");
    expect(campaign!.status).toBe("active");
  });

  it("lists campaigns with optional status filter", () => {
    campaignManager.create({ name: "A", goal: "Goal A" });
    campaignManager.create({ name: "B", goal: "Goal B" });

    const all = campaignManager.list();
    expect(all.length).toBe(2);

    const active = campaignManager.list("active");
    expect(active.length).toBe(2);
  });

  it("persists campaigns and campaign missions across manager restart", () => {
    const campaignId = campaignManager.create({ name: "Persist", goal: "Survive restart" });
    const missionId = missionManager.create({ name: "Mission", goal: "Persisted mission" });
    campaignManager.addMission(campaignId, missionId, { priority: 2 });

    campaignManager.close();
    missionManager.close();

    missionManager = new MissionManager(dbPath);
    campaignManager = new CampaignManager(missionManager);

    const campaign = campaignManager.get(campaignId);
    expect(campaign).not.toBeNull();
    expect(campaign!.name).toBe("Persist");
    expect(campaignManager.missions(campaignId)).toEqual([
      expect.objectContaining({
        campaignId,
        missionId,
        priority: 2,
      }),
    ]);
  });

  it("supports pause, resume, cancel", () => {
    const id = campaignManager.create({ name: "Control", goal: "Test controls" });

    campaignManager.pause(id);
    expect(campaignManager.get(id)!.status).toBe("paused");

    campaignManager.resume(id);
    expect(campaignManager.get(id)!.status).toBe("active");

    campaignManager.cancel(id);
    expect(campaignManager.get(id)!.status).toBe("canceled");
  });
});

// ---------------------------------------------------------------------------
// Campaign ↔ Mission relationships
// ---------------------------------------------------------------------------

describe("campaign-mission relationships", () => {
  it("adds missions to a campaign", () => {
    const campaignId = campaignManager.create({ name: "Multi", goal: "Multiple missions" });
    const m1 = missionManager.create({ name: "Mission 1", goal: "First" });
    const m2 = missionManager.create({ name: "Mission 2", goal: "Second" });

    campaignManager.addMission(campaignId, m1, { priority: 1 });
    campaignManager.addMission(campaignId, m2, { priority: 2 });

    const missions = campaignManager.missions(campaignId);
    expect(missions.length).toBe(2);
    expect(missions[0].missionId).toBe(m1);
    expect(missions[0].priority).toBe(1);
  });

  it("rejects nonexistent and duplicate mission IDs", () => {
    const campaignId = campaignManager.create({ name: "Validated", goal: "Validate membership" });
    const missionId = missionManager.create({ name: "Mission", goal: "Real mission" });

    expect(() => campaignManager.addMission(campaignId, "mission-does-not-exist")).toThrow(
      "Mission not found: mission-does-not-exist",
    );

    campaignManager.addMission(campaignId, missionId);
    expect(() => campaignManager.addMission(campaignId, missionId)).toThrow(
      `Mission already in campaign: ${missionId}`,
    );
  });

  it("removes a mission from a campaign", () => {
    const campaignId = campaignManager.create({ name: "Remove", goal: "Test removal" });
    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    campaignManager.addMission(campaignId, m1);

    expect(campaignManager.missions(campaignId).length).toBe(1);
    campaignManager.removeMission(campaignId, m1);
    expect(campaignManager.missions(campaignId).length).toBe(0);
  });

  it("respects mission ordering by priority", () => {
    const campaignId = campaignManager.create({ name: "Ordered", goal: "Test ordering" });
    const m1 = missionManager.create({ name: "Low", goal: "Low priority" });
    const m2 = missionManager.create({ name: "High", goal: "High priority" });

    campaignManager.addMission(campaignId, m1, { priority: 3 });
    campaignManager.addMission(campaignId, m2, { priority: 1 });

    const missions = campaignManager.missions(campaignId);
    expect(missions[0].missionId).toBe(m2); // Higher priority first
    expect(missions[1].missionId).toBe(m1);
  });

  it("supports depends-on relationships between missions", () => {
    const campaignId = campaignManager.create({ name: "Deps", goal: "Test deps" });
    const m1 = missionManager.create({ name: "Foundation", goal: "Build base" });
    const m2 = missionManager.create({ name: "Feature", goal: "Build feature" });

    campaignManager.addMission(campaignId, m1, { priority: 1 });
    campaignManager.addMission(campaignId, m2, { priority: 2, dependsOn: [m1] });

    const missions = campaignManager.missions(campaignId);
    const feature = missions.find((m) => m.missionId === m2);
    expect(feature!.dependsOn).toContain(m1);
  });

  it("rejects dependencies that are not already in the campaign", () => {
    const campaignId = campaignManager.create({ name: "Deps", goal: "Dependency validation" });
    const m1 = missionManager.create({ name: "Mission", goal: "Mission" });

    expect(() =>
      campaignManager.addMission(campaignId, m1, { dependsOn: ["mission-missing"] }),
    ).toThrow("Dependency mission not in campaign: mission-missing");
  });
});

// ---------------------------------------------------------------------------
// Campaign progress
// ---------------------------------------------------------------------------

describe("campaign progress", () => {
  it("reports progress based on mission completion", () => {
    const campaignId = campaignManager.create({ name: "Progress", goal: "Track progress" });
    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    const m2 = missionManager.create({ name: "M2", goal: "G2" });

    campaignManager.addMission(campaignId, m1);
    campaignManager.addMission(campaignId, m2);

    let progress = campaignManager.progress(campaignId);
    expect(progress.totalMissions).toBe(2);
    expect(progress.completedMissions).toBe(0);
    expect(progress.percentComplete).toBe(0);

    // Complete one mission
    missionManager.setStatus(m1, "completed");
    progress = campaignManager.progress(campaignId);
    expect(progress.completedMissions).toBe(1);
    expect(progress.percentComplete).toBe(50);

    // Complete both
    missionManager.setStatus(m2, "completed");
    progress = campaignManager.progress(campaignId);
    expect(progress.completedMissions).toBe(2);
    expect(progress.percentComplete).toBe(100);
  });

  it("reports aggregate step count across missions", () => {
    const campaignId = campaignManager.create({ name: "Steps", goal: "Count steps" });
    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    const m2 = missionManager.create({ name: "M2", goal: "G2" });

    campaignManager.addMission(campaignId, m1);
    campaignManager.addMission(campaignId, m2);

    missionManager.advance(m1, "Step 1 of M1");
    missionManager.advance(m1, "Step 2 of M1");
    missionManager.advance(m2, "Step 1 of M2");

    const progress = campaignManager.progress(campaignId);
    expect(progress.totalSteps).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// Campaign budget
// ---------------------------------------------------------------------------

describe("campaign budget", () => {
  it("tracks budget across missions", () => {
    const campaignId = campaignManager.create({
      name: "Budget",
      goal: "Budget tracking",
      budget: { maxMissions: 3, maxTotalSteps: 10 },
    });

    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    campaignManager.addMission(campaignId, m1);

    missionManager.advance(m1, "Step 1");
    missionManager.advance(m1, "Step 2");

    const budget = campaignManager.budgetUsage(campaignId);
    expect(budget.missionsUsed).toBe(1);
    expect(budget.maxMissions).toBe(3);
    expect(budget.totalStepsUsed).toBe(2);
    expect(budget.maxTotalSteps).toBe(10);
    expect(budget.exhausted).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Campaign completion
// ---------------------------------------------------------------------------

describe("campaign completion", () => {
  it("completes when all missions complete", () => {
    const campaignId = campaignManager.create({ name: "Complete", goal: "Test completion" });
    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    const m2 = missionManager.create({ name: "M2", goal: "G2" });

    campaignManager.addMission(campaignId, m1);
    campaignManager.addMission(campaignId, m2);

    missionManager.setStatus(m1, "completed");
    missionManager.setStatus(m2, "completed");

    // Campaign should auto-complete or can be explicitly verified
    const progress = campaignManager.progress(campaignId);
    expect(progress.allMissionsComplete).toBe(true);
    expect(campaignManager.get(campaignId)!.status).toBe("completed");
    expect(campaignManager.list("completed").map((campaign) => campaign.id)).toContain(campaignId);
  });

  it("reports failed when any mission fails", () => {
    const campaignId = campaignManager.create({ name: "Fail", goal: "Test failure" });
    const m1 = missionManager.create({ name: "M1", goal: "G1" });
    campaignManager.addMission(campaignId, m1);

    missionManager.setStatus(m1, "failed");

    const progress = campaignManager.progress(campaignId);
    expect(progress.failedMissions).toBe(1);
    expect(progress.allMissionsComplete).toBe(false);
    expect(campaignManager.get(campaignId)!.status).toBe("failed");
    expect(campaignManager.list("failed").map((campaign) => campaign.id)).toContain(campaignId);
  });
});

// ---------------------------------------------------------------------------
// Campaign types
// ---------------------------------------------------------------------------

describe("campaign types", () => {
  it("Campaign has required fields", () => {
    const id = campaignManager.create({ name: "Shape", goal: "Test shape" });
    const campaign: Campaign = campaignManager.get(id)!;

    expect(typeof campaign.id).toBe("string");
    expect(typeof campaign.name).toBe("string");
    expect(typeof campaign.goal).toBe("string");
    expect(typeof campaign.status).toBe("string");
    expect(typeof campaign.createdAt).toBe("string");
  });

  it("CampaignProgress has required fields", () => {
    const id = campaignManager.create({ name: "Progress shape", goal: "Test" });
    const progress: CampaignProgress = campaignManager.progress(id);

    expect(typeof progress.totalMissions).toBe("number");
    expect(typeof progress.completedMissions).toBe("number");
    expect(typeof progress.failedMissions).toBe("number");
    expect(typeof progress.totalSteps).toBe("number");
    expect(typeof progress.percentComplete).toBe("number");
    expect(typeof progress.allMissionsComplete).toBe("boolean");
  });
});
