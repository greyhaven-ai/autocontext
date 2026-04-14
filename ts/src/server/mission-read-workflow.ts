export type MissionReadResource = "detail" | "steps" | "subgoals" | "budget" | "artifacts";

export interface MissionReadManager {
  get(missionId: string): unknown | null;
}

export interface MissionReadApi {
  getMission(id: string): Record<string, unknown> | null;
  getMissionSteps(id: string): unknown;
  getMissionSubgoals(id: string): unknown;
  getMissionBudget(id: string): unknown;
  getMissionArtifacts(id: string): unknown;
}

export function executeMissionReadRequest(opts: {
  missionId: string;
  resource: MissionReadResource;
  missionManager: MissionReadManager;
  missionApi: MissionReadApi;
}): { status: number; body: unknown } {
  if (opts.resource === "detail") {
    const mission = opts.missionApi.getMission(opts.missionId);
    if (!mission) {
      return {
        status: 404,
        body: { error: `Mission '${opts.missionId}' not found` },
      };
    }
    return { status: 200, body: mission };
  }

  if (!opts.missionManager.get(opts.missionId)) {
    return {
      status: 404,
      body: { error: `Mission '${opts.missionId}' not found` },
    };
  }

  switch (opts.resource) {
    case "steps":
      return { status: 200, body: opts.missionApi.getMissionSteps(opts.missionId) };
    case "subgoals":
      return { status: 200, body: opts.missionApi.getMissionSubgoals(opts.missionId) };
    case "budget":
      return { status: 200, body: opts.missionApi.getMissionBudget(opts.missionId) };
    case "artifacts":
      return { status: 200, body: opts.missionApi.getMissionArtifacts(opts.missionId) };
  }
}
