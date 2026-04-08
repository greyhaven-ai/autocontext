/**
 * Campaign MCP tool definitions (AC-533).
 *
 * Mirrors the mission-tools.ts pattern: tool definitions + registration
 * function that binds them to a CampaignManager instance.
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { CampaignManager } from "../mission/campaign.js";
import { CampaignManager as CampaignManagerImpl } from "../mission/campaign.js";
import { MissionManager } from "../mission/manager.js";

export interface CampaignToolDef {
  name: string;
  description: string;
  schema: {
    type: "object";
    properties: Record<string, { type: string; description: string }>;
    required?: string[];
  };
}

export const CAMPAIGN_TOOLS: CampaignToolDef[] = [
  {
    name: "create_campaign",
    description: "Create a new campaign to coordinate multiple missions",
    schema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Campaign name" },
        goal: { type: "string", description: "Campaign goal / objective" },
        budget_tokens: {
          type: "number",
          description: "Max total steps budget (optional)",
        },
        budget_missions: {
          type: "number",
          description: "Max missions budget (optional)",
        },
      },
      required: ["name", "goal"],
    },
  },
  {
    name: "campaign_status",
    description: "Get campaign details with progress and mission list",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
      },
      required: ["campaign_id"],
    },
  },
  {
    name: "list_campaigns",
    description: "List all campaigns, optionally filtered by status",
    schema: {
      type: "object",
      properties: {
        status: {
          type: "string",
          description:
            "Filter by status: active, paused, completed, failed, canceled",
        },
      },
    },
  },
  {
    name: "add_campaign_mission",
    description:
      "Add a mission to a campaign with optional priority and dependencies",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
        mission_id: { type: "string", description: "Mission ID to add" },
        priority: {
          type: "number",
          description: "Priority (lower = higher priority)",
        },
        depends_on: {
          type: "string",
          description: "Comma-separated mission IDs this depends on",
        },
      },
      required: ["campaign_id", "mission_id"],
    },
  },
  {
    name: "campaign_progress",
    description:
      "Get campaign progress with completion percentage and budget usage",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
      },
      required: ["campaign_id"],
    },
  },
  {
    name: "pause_campaign",
    description: "Pause an active campaign",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
      },
      required: ["campaign_id"],
    },
  },
  {
    name: "resume_campaign",
    description: "Resume a paused campaign",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
      },
      required: ["campaign_id"],
    },
  },
  {
    name: "cancel_campaign",
    description: "Cancel a campaign",
    schema: {
      type: "object",
      properties: {
        campaign_id: { type: "string", description: "Campaign ID" },
      },
      required: ["campaign_id"],
    },
  },
];

export function registerCampaignTools(
  server: McpServer,
  opts: { dbPath: string },
): void {
  const withManager = async <T>(fn: (manager: CampaignManager) => Promise<T> | T): Promise<T> => {
    const missionManager = new MissionManager(opts.dbPath);
    const campaignManager = new CampaignManagerImpl(missionManager);
    try {
      return await fn(campaignManager);
    } finally {
      campaignManager.close();
      missionManager.close();
    }
  };

  server.tool(
    "create_campaign",
    {
      name: z.string(),
      goal: z.string(),
      budget_tokens: z.number().optional(),
      budget_missions: z.number().optional(),
    },
    async ({ name, goal, budget_tokens, budget_missions }) => withManager((mgr) => {
      const budget =
        budget_tokens || budget_missions
          ? {
              ...(budget_tokens ? { maxTotalSteps: budget_tokens } : {}),
              ...(budget_missions ? { maxMissions: budget_missions } : {}),
            }
          : undefined;
      const id = mgr.create({ name, goal, budget });
      return {
        content: [{ type: "text" as const, text: JSON.stringify({ id }) }],
      };
    }),
  );

  server.tool(
    "campaign_status",
    { campaign_id: z.string() },
    async ({ campaign_id }) => withManager((mgr) => {
      const campaign = mgr.get(campaign_id);
      if (!campaign)
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify({ error: "Campaign not found" }),
            },
          ],
        };
      const progress = mgr.progress(campaign_id);
      const missions = mgr.missions(campaign_id);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ ...campaign, progress, missions }),
          },
        ],
      };
    }),
  );

  server.tool(
    "list_campaigns",
    { status: z.string().optional() },
    async ({ status }) => withManager((mgr) => {
      const campaigns = mgr.list(status as Parameters<typeof mgr.list>[0]);
      return {
        content: [{ type: "text" as const, text: JSON.stringify(campaigns) }],
      };
    }),
  );

  server.tool(
    "add_campaign_mission",
    {
      campaign_id: z.string(),
      mission_id: z.string(),
      priority: z.number().optional(),
      depends_on: z.string().optional(),
    },
    async ({ campaign_id, mission_id, priority, depends_on }) => withManager((mgr) => {
      const dependsOn = depends_on
        ? depends_on.split(",").map((s) => s.trim())
        : undefined;
      mgr.addMission(campaign_id, mission_id, { priority, dependsOn });
      return {
        content: [
          { type: "text" as const, text: JSON.stringify({ ok: true }) },
        ],
      };
    }),
  );

  server.tool(
    "campaign_progress",
    { campaign_id: z.string() },
    async ({ campaign_id }) => withManager((mgr) => {
      const progress = mgr.progress(campaign_id);
      const budget = mgr.budgetUsage(campaign_id);
      return {
        content: [
          { type: "text" as const, text: JSON.stringify({ progress, budget }) },
        ],
      };
    }),
  );

  server.tool(
    "pause_campaign",
    { campaign_id: z.string() },
    async ({ campaign_id }) => withManager((mgr) => {
      mgr.pause(campaign_id);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ ok: true, status: "paused" }),
          },
        ],
      };
    }),
  );

  server.tool(
    "resume_campaign",
    { campaign_id: z.string() },
    async ({ campaign_id }) => withManager((mgr) => {
      mgr.resume(campaign_id);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ ok: true, status: "active" }),
          },
        ],
      };
    }),
  );

  server.tool(
    "cancel_campaign",
    { campaign_id: z.string() },
    async ({ campaign_id }) => withManager((mgr) => {
      mgr.cancel(campaign_id);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ ok: true, status: "canceled" }),
          },
        ],
      };
    }),
  );
}
