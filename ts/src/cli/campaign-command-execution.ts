import type { CampaignStatus } from "../mission/campaign.js";

import {
  buildCampaignAddMissionResult,
  buildCampaignProgressPayload,
  buildCampaignStatusDetail,
  validateCampaignLifecycleAction,
} from "./campaign-command-workflow.js";

export function executeCampaignCreateCommand<TCampaign>(opts: {
  manager: {
    create(input: { name: string; goal: string; budget?: { maxMissions?: number; maxTotalSteps?: number } }): string;
    get(id: string): TCampaign | null;
  };
  plan: {
    name: string;
    goal: string;
    budget?: { maxMissions?: number; maxTotalSteps?: number };
  };
}): TCampaign | null {
  const id = opts.manager.create(opts.plan);
  return opts.manager.get(id);
}

export function executeCampaignStatusCommand<TCampaign extends object, TProgress, TMission>(opts: {
  campaignId: string;
  getCampaign(id: string): TCampaign;
  getProgress(id: string): TProgress;
  getMissions(id: string): TMission[];
}): TCampaign & { progress: TProgress; missions: TMission[] } {
  return buildCampaignStatusDetail(
    opts.getCampaign(opts.campaignId),
    opts.getProgress(opts.campaignId),
    opts.getMissions(opts.campaignId),
  );
}

export function executeCampaignListCommand<TCampaign>(opts: {
  listCampaigns(status?: CampaignStatus): TCampaign[];
  status?: CampaignStatus;
}): TCampaign[] {
  return opts.listCampaigns(opts.status);
}

export function executeCampaignAddMissionCommand(opts: {
  addMission(
    campaignId: string,
    missionId: string,
    options: { priority?: number; dependsOn?: string[] },
  ): void;
  plan: {
    campaignId: string;
    missionId: string;
    options: { priority?: number; dependsOn?: string[] };
  };
}): { ok: true; campaignId: string; missionId: string } {
  opts.addMission(opts.plan.campaignId, opts.plan.missionId, opts.plan.options);
  return buildCampaignAddMissionResult(opts.plan.campaignId, opts.plan.missionId);
}

export function executeCampaignProgressCommand<TProgress, TBudgetUsage>(opts: {
  campaignId: string;
  getProgress(id: string): TProgress;
  getBudgetUsage(id: string): TBudgetUsage;
}): TProgress & { budgetUsage: TBudgetUsage } {
  return buildCampaignProgressPayload(
    opts.getProgress(opts.campaignId),
    opts.getBudgetUsage(opts.campaignId),
  );
}

export function executeCampaignLifecycleCommand<TCampaign extends { status: CampaignStatus }>(opts: {
  action: "pause" | "resume" | "cancel";
  campaignId: string;
  manager: {
    get(id: string): TCampaign;
    pause(id: string): void;
    resume(id: string): void;
    cancel(id: string): void;
  };
}): TCampaign {
  const campaign = opts.manager.get(opts.campaignId);
  validateCampaignLifecycleAction(opts.action, campaign.status);
  opts.manager[opts.action](opts.campaignId);
  return opts.manager.get(opts.campaignId);
}
