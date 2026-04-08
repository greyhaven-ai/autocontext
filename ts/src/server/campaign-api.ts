/**
 * Campaign REST API route handlers (AC-533).
 *
 * Mirrors the mission-api.ts pattern: pure functions returning data,
 * wired into the HTTP server by the caller.
 */

import type { CampaignManager } from "../mission/campaign.js";
import type {
  Campaign,
  CampaignProgress,
  CampaignMissionEntry,
  CampaignStatus,
} from "../mission/campaign.js";

export interface CampaignWithDetails extends Campaign {
  progress?: CampaignProgress;
  missions?: CampaignMissionEntry[];
}

export interface CampaignApiRoutes {
  listCampaigns(status?: string): Campaign[];
  getCampaign(id: string): CampaignWithDetails | null;
  createCampaign(opts: {
    name: string;
    goal: string;
    budgetTokens?: number;
    budgetCost?: number;
  }): { id: string };
  addMission(
    campaignId: string,
    opts: { missionId: string; priority?: number; dependsOn?: string[] },
  ): void;
  updateStatus(campaignId: string, status: string): void;
  getCampaignProgress(campaignId: string): CampaignProgress | null;
}

export function buildCampaignApiRoutes(
  manager: CampaignManager,
): CampaignApiRoutes {
  return {
    listCampaigns(status?: string) {
      return manager.list(status as CampaignStatus | undefined);
    },

    getCampaign(id: string): CampaignWithDetails | null {
      const campaign = manager.get(id);
      if (!campaign) return null;
      try {
        const progress = manager.progress(id);
        const missions = manager.missions(id);
        return { ...campaign, progress, missions };
      } catch {
        return campaign;
      }
    },

    createCampaign(opts) {
      const budget =
        opts.budgetTokens || opts.budgetCost
          ? {
              ...(opts.budgetTokens
                ? { maxTotalSteps: opts.budgetTokens }
                : {}),
              ...(opts.budgetCost ? { maxMissions: opts.budgetCost } : {}),
            }
          : undefined;
      const id = manager.create({ name: opts.name, goal: opts.goal, budget });
      return { id };
    },

    addMission(campaignId, opts) {
      manager.addMission(campaignId, opts.missionId, {
        priority: opts.priority,
        dependsOn: opts.dependsOn,
      });
    },

    updateStatus(campaignId, status) {
      switch (status) {
        case "paused":
          manager.pause(campaignId);
          break;
        case "active":
          manager.resume(campaignId);
          break;
        case "canceled":
          manager.cancel(campaignId);
          break;
        default:
          throw new Error(`Invalid status transition: ${status}`);
      }
    },

    getCampaignProgress(campaignId) {
      try {
        return manager.progress(campaignId);
      } catch {
        return null;
      }
    },
  };
}
