/**
 * Campaign abstraction — coordinating multiple missions (AC-428).
 *
 * A Campaign is a higher-order objective layer above missions.
 * It models long-term goals that require multiple coordinated missions:
 * - formalize an area of mathematics
 * - ship a product initiative with dependent missions
 * - close a family of related incidents or migrations
 *
 * Campaigns have their own lifecycle, budget tracking, progress aggregation,
 * and mission dependency graphs. They do not replace missions — they
 * compose them.
 */

import Database from "better-sqlite3";
import { randomUUID } from "node:crypto";
import type { MissionManager } from "./manager.js";
import type { MissionStatus } from "./types.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CampaignStatus = "active" | "paused" | "completed" | "failed" | "canceled";

export interface CampaignBudget {
  maxMissions?: number;
  maxTotalSteps?: number;
  maxTotalCostUsd?: number;
}

export interface Campaign {
  id: string;
  name: string;
  goal: string;
  status: CampaignStatus;
  budget?: CampaignBudget;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt?: string;
  completedAt?: string;
}

export interface CampaignMissionEntry {
  campaignId: string;
  missionId: string;
  priority: number;
  dependsOn: string[];
  addedAt: string;
}

export interface CampaignProgress {
  totalMissions: number;
  completedMissions: number;
  failedMissions: number;
  activeMissions: number;
  totalSteps: number;
  percentComplete: number;
  allMissionsComplete: boolean;
}

export interface CampaignBudgetUsage {
  missionsUsed: number;
  maxMissions?: number;
  totalStepsUsed: number;
  maxTotalSteps?: number;
  exhausted: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  return `campaign-${randomUUID().slice(0, 8)}`;
}

function isTerminalCampaignStatus(status: CampaignStatus): boolean {
  return status === "completed" || status === "failed" || status === "canceled";
}

function missionCountsAsFailure(status: MissionStatus): boolean {
  return status === "failed" || status === "verifier_failed" || status === "budget_exhausted";
}

function missionCountsAsActive(status: MissionStatus): boolean {
  return status === "active" || status === "paused" || status === "blocked";
}

// ---------------------------------------------------------------------------
// SQLite persistence
// ---------------------------------------------------------------------------

class CampaignStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
    this.createTables();
  }

  private createTables(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        goal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        budget TEXT,
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS campaign_missions (
        campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
        priority INTEGER NOT NULL,
        depends_on TEXT DEFAULT '[]',
        added_at TEXT NOT NULL,
        PRIMARY KEY (campaign_id, mission_id)
      );
    `);
  }

  createCampaign(opts: {
    name: string;
    goal: string;
    budget?: CampaignBudget;
    metadata?: Record<string, unknown>;
  }): string {
    const id = generateId();
    const createdAt = new Date().toISOString();
    this.db.prepare(
      `INSERT INTO campaigns (id, name, goal, status, budget, metadata, created_at)
       VALUES (?, ?, ?, 'active', ?, ?, ?)`,
    ).run(
      id,
      opts.name,
      opts.goal,
      opts.budget ? JSON.stringify(opts.budget) : null,
      JSON.stringify(opts.metadata ?? {}),
      createdAt,
    );
    return id;
  }

  getCampaign(id: string): Campaign | null {
    const row = this.db.prepare("SELECT * FROM campaigns WHERE id = ?").get(id) as Record<string, unknown> | undefined;
    if (!row) return null;
    return this.mapCampaign(row);
  }

  listCampaigns(): Campaign[] {
    const rows = this.db.prepare("SELECT * FROM campaigns ORDER BY created_at DESC").all() as Array<Record<string, unknown>>;
    return rows.map((row) => this.mapCampaign(row));
  }

  setStatus(campaignId: string, status: CampaignStatus): void {
    const completedAt = isTerminalCampaignStatus(status) ? new Date().toISOString() : null;
    this.db.prepare(
      "UPDATE campaigns SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
    ).run(status, new Date().toISOString(), completedAt, campaignId);
  }

  touchCampaign(campaignId: string): void {
    this.db.prepare("UPDATE campaigns SET updated_at = ? WHERE id = ?").run(new Date().toISOString(), campaignId);
  }

  addMission(
    campaignId: string,
    missionId: string,
    opts?: { priority?: number; dependsOn?: string[] },
  ): void {
    this.db.prepare(
      `INSERT INTO campaign_missions (campaign_id, mission_id, priority, depends_on, added_at)
       VALUES (?, ?, ?, ?, ?)`,
    ).run(
      campaignId,
      missionId,
      opts?.priority ?? this.missionCount(campaignId) + 1,
      JSON.stringify(opts?.dependsOn ?? []),
      new Date().toISOString(),
    );
    this.touchCampaign(campaignId);
  }

  removeMission(campaignId: string, missionId: string): void {
    this.db.prepare(
      "DELETE FROM campaign_missions WHERE campaign_id = ? AND mission_id = ?",
    ).run(campaignId, missionId);
    this.touchCampaign(campaignId);
  }

  missionCount(campaignId: string): number {
    const row = this.db.prepare(
      "SELECT COUNT(*) as count FROM campaign_missions WHERE campaign_id = ?",
    ).get(campaignId) as { count: number };
    return row.count;
  }

  hasMission(campaignId: string, missionId: string): boolean {
    const row = this.db.prepare(
      "SELECT 1 FROM campaign_missions WHERE campaign_id = ? AND mission_id = ?",
    ).get(campaignId, missionId);
    return Boolean(row);
  }

  missions(campaignId: string): CampaignMissionEntry[] {
    const rows = this.db.prepare(
      "SELECT * FROM campaign_missions WHERE campaign_id = ? ORDER BY priority ASC, added_at ASC",
    ).all(campaignId) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      campaignId: row.campaign_id as string,
      missionId: row.mission_id as string,
      priority: row.priority as number,
      dependsOn: JSON.parse((row.depends_on as string) ?? "[]"),
      addedAt: row.added_at as string,
    }));
  }

  close(): void {
    this.db.close();
  }

  private mapCampaign(row: Record<string, unknown>): Campaign {
    return {
      id: row.id as string,
      name: row.name as string,
      goal: row.goal as string,
      status: row.status as CampaignStatus,
      budget: row.budget ? JSON.parse(row.budget as string) : undefined,
      metadata: JSON.parse((row.metadata as string) ?? "{}"),
      createdAt: row.created_at as string,
      updatedAt: (row.updated_at as string) ?? undefined,
      completedAt: (row.completed_at as string) ?? undefined,
    };
  }
}

// ---------------------------------------------------------------------------
// CampaignManager
// ---------------------------------------------------------------------------

export class CampaignManager {
  private missionManager: MissionManager;
  private store: CampaignStore;

  constructor(missionManager: MissionManager) {
    this.missionManager = missionManager;
    this.store = new CampaignStore(missionManager.getDbPath());
  }

  /**
   * Create a new campaign.
   */
  create(opts: {
    name: string;
    goal: string;
    budget?: CampaignBudget;
    metadata?: Record<string, unknown>;
  }): string {
    return this.store.createCampaign(opts);
  }

  /**
   * Get a campaign by ID.
   */
  get(id: string): Campaign | null {
    this.reconcileStatus(id);
    return this.store.getCampaign(id);
  }

  /**
   * List campaigns, optionally filtered by status.
   */
  list(status?: CampaignStatus): Campaign[] {
    const all = this.store.listCampaigns();
    for (const campaign of all) {
      this.reconcileStatus(campaign.id);
    }

    const refreshed = this.store.listCampaigns();
    if (!status) return refreshed;
    return refreshed.filter((campaign) => campaign.status === status);
  }

  /**
   * Add a mission to a campaign.
   */
  addMission(
    campaignId: string,
    missionId: string,
    opts?: { priority?: number; dependsOn?: string[] },
  ): void {
    const campaign = this.store.getCampaign(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);
    if (!this.missionManager.get(missionId)) throw new Error(`Mission not found: ${missionId}`);
    if (this.store.hasMission(campaignId, missionId)) {
      throw new Error(`Mission already in campaign: ${missionId}`);
    }

    for (const dependencyId of opts?.dependsOn ?? []) {
      if (dependencyId === missionId) {
        throw new Error(`Mission cannot depend on itself: ${missionId}`);
      }
      if (!this.store.hasMission(campaignId, dependencyId)) {
        throw new Error(`Dependency mission not in campaign: ${dependencyId}`);
      }
    }

    this.store.addMission(campaignId, missionId, opts);
    this.reconcileStatus(campaignId);
  }

  /**
   * Remove a mission from a campaign.
   */
  removeMission(campaignId: string, missionId: string): void {
    const campaign = this.store.getCampaign(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);
    this.store.removeMission(campaignId, missionId);
    this.reconcileStatus(campaignId);
  }

  /**
   * Get missions in a campaign, ordered by priority.
   */
  missions(campaignId: string): CampaignMissionEntry[] {
    const campaign = this.store.getCampaign(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);
    return this.store.missions(campaignId);
  }

  /**
   * Get campaign progress aggregated from mission statuses.
   */
  progress(campaignId: string): CampaignProgress {
    const campaign = this.reconcileStatus(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);

    const entries = this.store.missions(campaignId);
    let completed = 0;
    let failed = 0;
    let active = 0;
    let totalSteps = 0;

    for (const entry of entries) {
      const mission = this.missionManager.get(entry.missionId);
      if (!mission) continue;

      if (mission.status === "completed") completed++;
      else if (missionCountsAsFailure(mission.status)) failed++;
      else if (missionCountsAsActive(mission.status)) active++;

      totalSteps += this.missionManager.steps(entry.missionId).length;
    }

    const total = entries.length;
    return {
      totalMissions: total,
      completedMissions: completed,
      failedMissions: failed,
      activeMissions: active,
      totalSteps,
      percentComplete: total > 0 ? Math.round((completed / total) * 100) : 0,
      allMissionsComplete: total > 0 && completed === total,
    };
  }

  /**
   * Get campaign budget usage aggregated from missions.
   */
  budgetUsage(campaignId: string): CampaignBudgetUsage {
    const campaign = this.reconcileStatus(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);

    const entries = this.store.missions(campaignId);
    let totalSteps = 0;
    for (const entry of entries) {
      totalSteps += this.missionManager.steps(entry.missionId).length;
    }

    const maxMissions = campaign.budget?.maxMissions;
    const maxTotalSteps = campaign.budget?.maxTotalSteps;
    const exhausted =
      (maxMissions != null && entries.length >= maxMissions) ||
      (maxTotalSteps != null && totalSteps >= maxTotalSteps);

    return {
      missionsUsed: entries.length,
      maxMissions,
      totalStepsUsed: totalSteps,
      maxTotalSteps,
      exhausted,
    };
  }

  /**
   * Pause the campaign.
   */
  pause(campaignId: string): void {
    this.setStatus(campaignId, "paused");
  }

  /**
   * Resume the campaign.
   */
  resume(campaignId: string): void {
    this.setStatus(campaignId, "active");
  }

  /**
   * Cancel the campaign.
   */
  cancel(campaignId: string): void {
    this.setStatus(campaignId, "canceled");
  }

  close(): void {
    this.store.close();
  }

  private reconcileStatus(campaignId: string): Campaign | null {
    const campaign = this.store.getCampaign(campaignId);
    if (!campaign) return null;
    if (campaign.status === "canceled") return campaign;

    const entries = this.store.missions(campaignId);
    let completed = 0;
    let failed = 0;

    for (const entry of entries) {
      const mission = this.missionManager.get(entry.missionId);
      if (!mission) continue;
      if (mission.status === "completed") completed++;
      else if (missionCountsAsFailure(mission.status)) failed++;
    }

    const total = entries.length;
    let nextStatus: CampaignStatus;
    if (total > 0 && completed === total) {
      nextStatus = "completed";
    } else if (failed > 0) {
      nextStatus = "failed";
    } else if (campaign.status === "paused") {
      nextStatus = "paused";
    } else {
      nextStatus = "active";
    }

    if (nextStatus !== campaign.status) {
      this.store.setStatus(campaignId, nextStatus);
      return this.store.getCampaign(campaignId);
    }

    return campaign;
  }

  private setStatus(campaignId: string, status: CampaignStatus): void {
    const campaign = this.store.getCampaign(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);
    this.store.setStatus(campaignId, status);
  }
}
