/**
 * `mission` command (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import { errorMessage } from "./shared.js";

// ---------------------------------------------------------------------------
// Mission CLI (AC-413)
// ---------------------------------------------------------------------------

export async function cmdMission(dbPath: string): Promise<void> {
  const subcommand = process.argv[3];
  const { MissionManager } = await import("../../mission/manager.js");
  const { createCodeMission } = await import("../../mission/verifiers.js");
  const {
    buildMissionArtifactsPayload,
    buildMissionStatusPayload,
    requireMission,
    runMissionLoop,
    writeMissionCheckpoint,
  } = await import("../../mission/control-plane.js");
  const {
    getMissionIdOrThrow,
    MISSION_HELP_TEXT,
    planMissionCreate,
    planMissionList,
    planMissionRun,
  } = await import("../mission-command-workflow.js");
  const {
    executeMissionArtifactsCommand,
    executeMissionCreateCommand,
    executeMissionLifecycleCommand,
    executeMissionListCommand,
    executeMissionRunCommand,
    executeMissionStatusCommand,
  } = await import("../mission-command-execution.js");
  const { loadSettings } = await import("../../config/index.js");
  const settings = loadSettings();
  const runsRoot = resolve(settings.runsRoot);

  if (!subcommand || subcommand === "--help" || subcommand === "-h") {
    console.log(MISSION_HELP_TEXT);
    process.exit(0);
  }

  const manager = new MissionManager(dbPath);
  try {
    switch (subcommand) {
      case "create": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            type: { type: "string" },
            name: { type: "string" },
            goal: { type: "string" },
            "max-steps": { type: "string" },
            "repo-path": { type: "string" },
            "test-command": { type: "string" },
            "lint-command": { type: "string" },
            "build-command": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planMissionCreate(values, resolve);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }

        console.log(
          JSON.stringify(
            executeMissionCreateCommand({
              manager,
              createCodeMission,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "run": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            id: { type: "string" },
            "max-iterations": { type: "string", default: "1" },
            "step-description": { type: "string" },
          },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission run --id <mission-id> [--max-iterations N] [--step-description <text>]",
        );
        const mission = requireMission(manager, missionId);
        const plan = planMissionRun(values, mission);
        const payload = await executeMissionRunCommand({
          manager,
          plan,
          runsRoot,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          createAdaptiveProvider: async () => {
            if (!plan.needsAdaptivePlanning) {
              return undefined;
            }
            const { createProvider, resolveProviderConfig } =
              await import("../../providers/index.js");
            return createProvider(resolveProviderConfig());
          },
          runMissionLoop,
        });
        console.log(JSON.stringify(payload, null, 2));
        break;
      }
      case "status": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission status --id <mission-id>",
        );
        console.log(
          JSON.stringify(
            executeMissionStatusCommand({
              manager,
              missionId,
              buildMissionStatusPayload,
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
        type MissionStatusParam = Parameters<typeof manager.list>[0];
        const plan = planMissionList(values);
        console.log(
          JSON.stringify(
            executeMissionListCommand({
              listMissions: (status) => manager.list(status as MissionStatusParam),
              status: plan.status as MissionStatusParam,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "artifacts": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission artifacts --id <mission-id>",
        );
        console.log(
          JSON.stringify(
            executeMissionArtifactsCommand({
              manager,
              missionId,
              runsRoot,
              buildMissionArtifactsPayload,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "pause": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission pause --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "pause",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "resume": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission resume --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "resume",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "cancel": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission cancel --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "cancel",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      default:
        console.error(`Unknown mission subcommand: ${subcommand}. Run 'autoctx mission --help'.`);
        process.exit(1);
    }
  } finally {
    manager.close();
  }
}
