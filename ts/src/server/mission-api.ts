/**
 * Mission REST API route handlers (AC-414).
 *
 * Pure functions returning data — the HTTP server wires these
 * into its request handler with appropriate status codes.
 */

import { buildMissionArtifactsPayload, buildMissionStatusPayload } from "../mission/control-plane.js";
import type { MissionManager } from "../mission/manager.js";
import type { Mission, MissionStep, MissionSubgoal, BudgetUsage } from "../mission/types.js";

export interface MissionApiRoutes {
  listMissions(status?: string): Mission[];
  getMission(id: string): Record<string, unknown> | null;
  getMissionSteps(id: string): MissionStep[];
  getMissionSubgoals(id: string): MissionSubgoal[];
  getMissionBudget(id: string): BudgetUsage;
  getMissionArtifacts(id: string): Record<string, unknown>;
}

export function buildMissionApiRoutes(manager: MissionManager, runsRoot: string): MissionApiRoutes {
  return {
    listMissions(status?: string) {
      type StatusParam = Parameters<typeof manager.list>[0];
      return manager.list(status as StatusParam);
    },

    getMission(id: string) {
      const mission = manager.get(id);
      if (!mission) return null;
      return buildMissionStatusPayload(manager, id);
    },

    getMissionSteps(id: string) {
      return manager.steps(id);
    },

    getMissionSubgoals(id: string) {
      return manager.subgoals(id);
    },

    getMissionBudget(id: string) {
      return manager.budgetUsage(id);
    },

    getMissionArtifacts(id: string) {
      return buildMissionArtifactsPayload(manager, id, runsRoot);
    },
  };
}
