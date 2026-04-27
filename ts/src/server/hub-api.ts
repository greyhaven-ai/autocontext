import { ResearchHubError, ResearchHubService } from "../knowledge/research-hub.js";
import type { SQLiteStore } from "../storage/index.js";

export interface HubApiResponse {
  status: number;
  body: unknown;
}

export interface HubApiRoutes {
  listSessions(): HubApiResponse;
  getSession(sessionId: string): HubApiResponse;
  upsertSession(sessionId: string, body: Record<string, unknown>): HubApiResponse;
  heartbeatSession(sessionId: string, body: Record<string, unknown>): HubApiResponse;
  promotePackageFromRun(runId: string, body: Record<string, unknown>): HubApiResponse;
  listPackages(): HubApiResponse;
  getPackage(packageId: string): HubApiResponse;
  adoptPackage(packageId: string, body: Record<string, unknown>): HubApiResponse;
  materializeResultFromRun(runId: string, body: Record<string, unknown>): HubApiResponse;
  listResults(): HubApiResponse;
  getResult(resultId: string): HubApiResponse;
  createPromotion(body: Record<string, unknown>): HubApiResponse;
  feed(): HubApiResponse;
}

export function buildHubApiRoutes(opts: {
  runsRoot: string;
  knowledgeRoot: string;
  skillsRoot: string;
  openStore: () => SQLiteStore;
}): HubApiRoutes {
  const service = new ResearchHubService(opts);
  return {
    listSessions: () => ({ status: 200, body: service.listSessions() }),
    getSession: (sessionId) => mapHubError(() => ({ status: 200, body: service.getSession(sessionId) })),
    upsertSession: (sessionId, body) => mapHubError(
      () => ({ status: 200, body: service.upsertSession(sessionId, body) }),
    ),
    heartbeatSession: (sessionId, body) => mapHubError(
      () => ({ status: 200, body: service.heartbeatSession(sessionId, body) }),
    ),
    promotePackageFromRun: (runId, body) => mapHubError(
      () => ({ status: 200, body: service.promotePackageFromRun(runId, body) }),
    ),
    listPackages: () => ({ status: 200, body: service.listPackages() }),
    getPackage: (packageId) => mapHubError(() => ({ status: 200, body: service.getPackage(packageId) })),
    adoptPackage: (packageId, body) => mapHubError(
      () => ({ status: 200, body: service.adoptPackage(packageId, body) }),
    ),
    materializeResultFromRun: (runId, body) => mapHubError(
      () => ({ status: 200, body: service.materializeResultFromRun(runId, body) }),
    ),
    listResults: () => ({ status: 200, body: service.listResults() }),
    getResult: (resultId) => mapHubError(() => ({ status: 200, body: service.getResult(resultId) })),
    createPromotion: (body) => mapHubError(() => ({ status: 200, body: service.createPromotion(body) })),
    feed: () => ({ status: 200, body: service.feed() }),
  };
}

function mapHubError(fn: () => HubApiResponse): HubApiResponse {
  try {
    return fn();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      status: error instanceof ResearchHubError ? error.status : 500,
      body: { detail: message },
    };
  }
}
