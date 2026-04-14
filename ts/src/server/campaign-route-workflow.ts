import type { CampaignApiRoutes } from "./campaign-api.js";

export type CampaignRouteKind = "list" | "create" | "detail" | "progress" | "add_mission" | "status";
export type CampaignRouteAction = "pause" | "resume" | "cancel";

export function buildCampaignCreateRequest(body: Record<string, unknown>): {
  name: string;
  goal: string;
  budgetTokens: number | undefined;
  budgetCost: number | undefined;
} {
  return {
    name: String(body.name ?? ""),
    goal: String(body.goal ?? ""),
    budgetTokens: typeof body.budgetTokens === "number" ? body.budgetTokens : undefined,
    budgetCost: typeof body.budgetCost === "number" ? body.budgetCost : undefined,
  };
}

export function buildCampaignMissionLinkRequest(body: Record<string, unknown>): {
  missionId: string;
  priority: number | undefined;
  dependsOn: string[] | undefined;
} {
  return {
    missionId: String(body.missionId ?? ""),
    priority: typeof body.priority === "number" ? body.priority : undefined,
    dependsOn: Array.isArray(body.dependsOn)
      ? body.dependsOn.filter((value): value is string => typeof value === "string")
      : undefined,
  };
}

export function executeCampaignRouteRequest(opts: {
  route: CampaignRouteKind;
  campaignApi: CampaignApiRoutes;
  campaignManager: { budgetUsage(campaignId: string): unknown };
  campaignId?: string;
  queryStatus?: string;
  action?: CampaignRouteAction;
  body: Record<string, unknown>;
}): { status: number; body: unknown } {
  switch (opts.route) {
    case "list":
      return { status: 200, body: opts.campaignApi.listCampaigns(opts.queryStatus) };
    case "create":
      return { status: 200, body: opts.campaignApi.createCampaign(buildCampaignCreateRequest(opts.body)) };
    case "detail": {
      const campaign = opts.campaignApi.getCampaign(opts.campaignId!);
      if (!campaign) {
        return { status: 404, body: { error: `Campaign '${opts.campaignId}' not found` } };
      }
      return { status: 200, body: campaign };
    }
    case "progress": {
      const progress = opts.campaignApi.getCampaignProgress(opts.campaignId!);
      if (!progress) {
        return { status: 404, body: { error: `Campaign '${opts.campaignId}' not found` } };
      }
      return {
        status: 200,
        body: {
          progress,
          budget: opts.campaignManager.budgetUsage(opts.campaignId!),
        },
      };
    }
    case "add_mission":
      opts.campaignApi.addMission(opts.campaignId!, buildCampaignMissionLinkRequest(opts.body));
      return { status: 200, body: { ok: true } };
    case "status": {
      const campaign = opts.campaignApi.getCampaign(opts.campaignId!);
      if (!campaign) {
        return { status: 404, body: { error: `Campaign '${opts.campaignId}' not found` } };
      }
      const status = opts.action === "pause"
        ? "paused"
        : opts.action === "resume"
          ? "active"
          : "canceled";
      opts.campaignApi.updateStatus(opts.campaignId!, status);
      return { status: 200, body: { ok: true, status } };
    }
  }
}
