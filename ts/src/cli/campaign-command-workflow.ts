import type { CampaignStatus } from "../mission/campaign.js";

export const CAMPAIGN_HELP_TEXT = `autoctx campaign — Manage multi-mission campaigns

Subcommands:
  create       Create a new campaign
  status       Show campaign details with progress
  list         List all campaigns
  add-mission  Add a mission to a campaign
  progress     Show campaign progress and budget usage
  pause        Pause an active campaign
  resume       Resume a paused campaign
  cancel       Cancel a campaign

Examples:
  autoctx campaign create --name "Q2 Goals" --goal "Ship OAuth and billing"
  autoctx campaign create --name "Budgeted" --goal "Test" --max-missions 5 --max-steps 50
  autoctx campaign list --status active
  autoctx campaign status --id <campaign-id>
  autoctx campaign add-mission --id <campaign-id> --mission-id <mission-id>
  autoctx campaign progress --id <campaign-id>

See also: mission, run`;

const ALLOWED_CAMPAIGN_STATUSES: CampaignStatus[] = [
  "active",
  "paused",
  "completed",
  "failed",
  "canceled",
];

export interface CampaignCreateValues {
  name?: string;
  goal?: string;
  "max-missions"?: string;
  "max-steps"?: string;
}

export interface CampaignAddMissionValues {
  id?: string;
  "mission-id"?: string;
  priority?: string;
  "depends-on"?: string;
}

export function getCampaignIdOrThrow(
  values: { id?: string },
  usage: string,
): string {
  if (!values.id) {
    throw new Error(usage);
  }
  return values.id;
}

export function planCampaignCreate(
  values: CampaignCreateValues,
  parsePositiveInteger: (raw: string | undefined, label: string) => number,
): {
  name: string;
  goal: string;
  budget?: {
    maxMissions?: number;
    maxTotalSteps?: number;
  };
} {
  if (!values.name || !values.goal) {
    throw new Error(
      "Usage: autoctx campaign create --name <name> --goal <goal> [--max-missions N] [--max-steps N]",
    );
  }

  const budget =
    values["max-missions"] || values["max-steps"]
      ? {
          ...(values["max-missions"]
            ? {
                maxMissions: parsePositiveInteger(
                  values["max-missions"],
                  "--max-missions",
                ),
              }
            : {}),
          ...(values["max-steps"]
            ? {
                maxTotalSteps: parsePositiveInteger(
                  values["max-steps"],
                  "--max-steps",
                ),
              }
            : {}),
        }
      : undefined;

  return {
    name: values.name,
    goal: values.goal,
    budget,
  };
}

export function parseCampaignStatus(
  raw: string | undefined,
): CampaignStatus | undefined {
  if (!raw) {
    return undefined;
  }
  if (!ALLOWED_CAMPAIGN_STATUSES.includes(raw as CampaignStatus)) {
    throw new Error(
      `Error: --status must be one of ${ALLOWED_CAMPAIGN_STATUSES.join(", ")}`,
    );
  }
  return raw as CampaignStatus;
}

export function planCampaignAddMission(
  values: CampaignAddMissionValues,
  parsePositiveInteger: (raw: string | undefined, label: string) => number,
): {
  campaignId: string;
  missionId: string;
  options: {
    priority?: number;
    dependsOn?: string[];
  };
} {
  if (!values.id || !values["mission-id"]) {
    throw new Error(
      "Usage: autoctx campaign add-mission --id <campaign-id> --mission-id <mission-id> [--priority N] [--depends-on <id>]",
    );
  }

  return {
    campaignId: values.id,
    missionId: values["mission-id"],
    options: {
      ...(values.priority
        ? { priority: parsePositiveInteger(values.priority, "--priority") }
        : {}),
      ...(values["depends-on"]
        ? { dependsOn: [values["depends-on"]] }
        : {}),
    },
  };
}

export function validateCampaignLifecycleAction(
  action: "pause" | "resume" | "cancel",
  status: CampaignStatus,
): void {
  if (action === "pause" && status !== "active") {
    throw new Error(`Cannot pause campaign in status: ${status}`);
  }
  if (action === "resume" && status !== "paused") {
    throw new Error(`Cannot resume campaign in status: ${status}`);
  }
  if (action === "cancel" && status !== "active" && status !== "paused") {
    throw new Error(`Cannot cancel campaign in status: ${status}`);
  }
}

export function buildCampaignStatusDetail<TCampaign, TProgress, TMission>(
  campaign: TCampaign,
  progress: TProgress,
  missions: TMission[],
): TCampaign & { progress: TProgress; missions: TMission[] } {
  return {
    ...campaign,
    progress,
    missions,
  };
}

export function buildCampaignProgressPayload<TProgress, TBudgetUsage>(
  progress: TProgress,
  budgetUsage: TBudgetUsage,
): TProgress & { budgetUsage: TBudgetUsage } {
  return {
    ...progress,
    budgetUsage,
  };
}

export function buildCampaignAddMissionResult(
  campaignId: string,
  missionId: string,
): { ok: true; campaignId: string; missionId: string } {
  return {
    ok: true,
    campaignId,
    missionId,
  };
}
