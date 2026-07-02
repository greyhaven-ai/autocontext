/**
 * `campaign` command (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import type { CampaignStatus } from "../../mission/campaign.js";
import { errorMessage, formatFatalCliError, parsePositiveInteger } from "./shared.js";

// ---------------------------------------------------------------------------
// campaign command (AC-533)
// ---------------------------------------------------------------------------

export async function cmdCampaign(dbPath: string): Promise<void> {
  const subcommand = process.argv[3];
  const { MissionManager } = await import("../../mission/manager.js");
  const { CampaignManager } = await import("../../mission/campaign.js");
  const {
    CAMPAIGN_HELP_TEXT,
    getCampaignIdOrThrow,
    parseCampaignStatus,
    planCampaignAddMission,
    planCampaignCreate,
  } = await import("../campaign-command-workflow.js");
  const {
    executeCampaignAddMissionCommand,
    executeCampaignCreateCommand,
    executeCampaignLifecycleCommand,
    executeCampaignListCommand,
    executeCampaignProgressCommand,
    executeCampaignStatusCommand,
  } = await import("../campaign-command-execution.js");

  if (!subcommand || subcommand === "--help" || subcommand === "-h") {
    console.log(CAMPAIGN_HELP_TEXT);
    process.exit(0);
  }

  const missionManager = new MissionManager(dbPath);
  const manager = new CampaignManager(missionManager);

  function requireCampaign(id: string) {
    const campaign = manager.get(id);
    if (!campaign) {
      console.error(`Campaign not found: ${id}`);
      process.exit(1);
    }
    return campaign;
  }

  function parseCampaignPositiveInteger(raw: string | undefined, label: string): number {
    try {
      return parsePositiveInteger(raw, label);
    } catch (error) {
      console.error(formatFatalCliError(error));
      process.exit(1);
    }
  }

  try {
    switch (subcommand) {
      case "create": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            name: { type: "string" },
            goal: { type: "string" },
            "max-missions": { type: "string" },
            "max-steps": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planCampaignCreate(values, parseCampaignPositiveInteger);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        console.log(
          JSON.stringify(
            executeCampaignCreateCommand({
              manager,
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "status": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(values, "Usage: autoctx campaign status --id <campaign-id>");
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        console.log(
          JSON.stringify(
            executeCampaignStatusCommand({
              campaignId: id,
              getCampaign: requireCampaign,
              getProgress: (campaignId) => manager.progress(campaignId),
              getMissions: (campaignId) => manager.missions(campaignId),
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "list": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { status: { type: "string" } },
        });
        let status: CampaignStatus | undefined;
        try {
          status = parseCampaignStatus(values.status);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        console.log(
          JSON.stringify(
            executeCampaignListCommand({
              listCampaigns: (campaignStatus) => manager.list(campaignStatus),
              status,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "add-mission": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            id: { type: "string" },
            "mission-id": { type: "string" },
            priority: { type: "string" },
            "depends-on": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planCampaignAddMission(values, parseCampaignPositiveInteger);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(plan.campaignId);
        console.log(
          JSON.stringify(
            executeCampaignAddMissionCommand({
              addMission: (campaignId, missionId, options) =>
                manager.addMission(campaignId, missionId, options),
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "progress": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(values, "Usage: autoctx campaign progress --id <campaign-id>");
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        console.log(
          JSON.stringify(
            executeCampaignProgressCommand({
              campaignId: id,
              getProgress: (campaignId) => manager.progress(campaignId),
              getBudgetUsage: (campaignId) => manager.budgetUsage(campaignId),
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "pause":
      case "resume":
      case "cancel": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(
            values,
            `Usage: autoctx campaign ${subcommand} --id <campaign-id>`,
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        try {
          console.log(
            JSON.stringify(
              executeCampaignLifecycleCommand({
                action: subcommand,
                campaignId: id,
                manager: {
                  get: requireCampaign,
                  pause: (campaignId) => manager.pause(campaignId),
                  resume: (campaignId) => manager.resume(campaignId),
                  cancel: (campaignId) => manager.cancel(campaignId),
                },
              }),
              null,
              2,
            ),
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        break;
      }
      default:
        console.error(`Unknown campaign subcommand: ${subcommand}. Run 'autoctx campaign --help'.`);
        process.exit(1);
    }
  } finally {
    manager.close();
    missionManager.close();
  }
}
