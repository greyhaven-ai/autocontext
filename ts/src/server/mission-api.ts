/**
 * Mission REST API route handlers (AC-414).
 *
 * Pure functions returning data — the HTTP server wires these
 * into its request handler with appropriate status codes.
 */

import type { MissionManager } from "../mission/manager.js";
import type { Mission, MissionStep, MissionSubgoal, BudgetUsage } from "../mission/types.js";

export interface MissionApiRoutes {
  listMissions(status?: string): Mission[];
  getMission(id: string): (Mission & { stepsCount: number }) | null;
  getMissionSteps(id: string): MissionStep[];
  getMissionSubgoals(id: string): MissionSubgoal[];
  getMissionBudget(id: string): BudgetUsage;
}

export function buildMissionApiRoutes(manager: MissionManager): MissionApiRoutes {
  return {
    listMissions(status?: string) {
      type StatusParam = Parameters<typeof manager.list>[0];
      return manager.list(status as StatusParam);
    },

    getMission(id: string) {
      const mission = manager.get(id);
      if (!mission) return null;
      const steps = manager.steps(id);
      return { ...mission, stepsCount: steps.length };
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
  };
}
