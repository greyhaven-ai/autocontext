/**
 * MCP tool definitions for mission control plane (AC-413).
 *
 * Exposes mission lifecycle operations as MCP-compatible tool schemas.
 * Actual execution is handled by MissionManager; these define the
 * contract that MCP servers wire up.
 */

export interface MissionToolDef {
  name: string;
  description: string;
  schema: {
    type: "object";
    properties: Record<string, { type: string; description: string }>;
    required?: string[];
  };
}

export const MISSION_TOOLS: MissionToolDef[] = [
  {
    name: "create_mission",
    description: "Create a new verifier-driven mission",
    schema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Mission name" },
        goal: { type: "string", description: "Mission goal / objective" },
        max_steps: { type: "number", description: "Maximum steps budget (optional)" },
      },
      required: ["name", "goal"],
    },
  },
  {
    name: "mission_status",
    description: "Get the status and details of a mission",
    schema: {
      type: "object",
      properties: {
        mission_id: { type: "string", description: "Mission ID" },
      },
      required: ["mission_id"],
    },
  },
  {
    name: "mission_list",
    description: "List all missions, optionally filtered by status",
    schema: {
      type: "object",
      properties: {
        status: { type: "string", description: "Filter by status (active, paused, completed, etc.)" },
      },
    },
  },
  {
    name: "pause_mission",
    description: "Pause an active mission",
    schema: {
      type: "object",
      properties: {
        mission_id: { type: "string", description: "Mission ID to pause" },
      },
      required: ["mission_id"],
    },
  },
  {
    name: "resume_mission",
    description: "Resume a paused mission",
    schema: {
      type: "object",
      properties: {
        mission_id: { type: "string", description: "Mission ID to resume" },
      },
      required: ["mission_id"],
    },
  },
  {
    name: "cancel_mission",
    description: "Cancel a mission",
    schema: {
      type: "object",
      properties: {
        mission_id: { type: "string", description: "Mission ID to cancel" },
      },
      required: ["mission_id"],
    },
  },
  {
    name: "mission_steps",
    description: "List steps taken in a mission",
    schema: {
      type: "object",
      properties: {
        mission_id: { type: "string", description: "Mission ID" },
      },
      required: ["mission_id"],
    },
  },
];
