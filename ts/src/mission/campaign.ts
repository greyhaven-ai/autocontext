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
// In-memory store (campaigns are lightweight — no SQLite needed yet)
// ---------------------------------------------------------------------------

function generateId(): string {
  return `campaign_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------------------------------------------------------------------------
// CampaignManager
// ---------------------------------------------------------------------------

export class CampaignManager {
  private missionManager: MissionManager;
  private campaigns = new Map<string, Campaign>();
  private campaignMissions = new Map<string, CampaignMissionEntry[]>();

  constructor(missionManager: MissionManager) {
    this.missionManager = missionManager;
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
    const id = generateId();
    const campaign: Campaign = {
      id,
      name: opts.name,
      goal: opts.goal,
      status: "active",
      budget: opts.budget,
      metadata: opts.metadata ?? {},
      createdAt: new Date().toISOString(),
    };
    this.campaigns.set(id, campaign);
    this.campaignMissions.set(id, []);
    return id;
  }

  /**
   * Get a campaign by ID.
   */
  get(id: string): Campaign | null {
    return this.campaigns.get(id) ?? null;
  }

  /**
   * List campaigns, optionally filtered by status.
   */
  list(status?: CampaignStatus): Campaign[] {
    const all = [...this.campaigns.values()];
    if (!status) return all;
    return all.filter((c) => c.status === status);
  }

  /**
   * Add a mission to a campaign.
   */
  addMission(
    campaignId: string,
    missionId: string,
    opts?: { priority?: number; dependsOn?: string[] },
  ): void {
    const entries = this.campaignMissions.get(campaignId);
    if (!entries) throw new Error(`Campaign not found: ${campaignId}`);

    entries.push({
      campaignId,
      missionId,
      priority: opts?.priority ?? entries.length + 1,
      dependsOn: opts?.dependsOn ?? [],
      addedAt: new Date().toISOString(),
    });

    // Sort by priority
    entries.sort((a, b) => a.priority - b.priority);
  }

  /**
   * Remove a mission from a campaign.
   */
  removeMission(campaignId: string, missionId: string): void {
    const entries = this.campaignMissions.get(campaignId);
    if (!entries) return;
    const idx = entries.findIndex((e) => e.missionId === missionId);
    if (idx !== -1) entries.splice(idx, 1);
  }

  /**
   * Get missions in a campaign, ordered by priority.
   */
  missions(campaignId: string): CampaignMissionEntry[] {
    return [...(this.campaignMissions.get(campaignId) ?? [])];
  }

  /**
   * Get campaign progress aggregated from mission statuses.
   */
  progress(campaignId: string): CampaignProgress {
    const entries = this.campaignMissions.get(campaignId) ?? [];
    let completed = 0;
    let failed = 0;
    let active = 0;
    let totalSteps = 0;

    for (const entry of entries) {
      const mission = this.missionManager.get(entry.missionId);
      if (!mission) continue;

      if (mission.status === "completed") completed++;
      else if (mission.status === "failed") failed++;
      else if (mission.status === "active") active++;

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
    const campaign = this.campaigns.get(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);

    const entries = this.campaignMissions.get(campaignId) ?? [];
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

  private setStatus(campaignId: string, status: CampaignStatus): void {
    const campaign = this.campaigns.get(campaignId);
    if (!campaign) throw new Error(`Campaign not found: ${campaignId}`);
    campaign.status = status;
    campaign.updatedAt = new Date().toISOString();
    if (status === "completed" || status === "failed" || status === "canceled") {
      campaign.completedAt = new Date().toISOString();
    }
  }
}
