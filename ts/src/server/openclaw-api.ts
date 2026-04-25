import type { AppSettings } from "../config/index.js";
import { DistillJobError } from "../openclaw/distill-job-store.js";
import { OpenClawService } from "../openclaw/service.js";
import type { SQLiteStore } from "../storage/index.js";

export interface OpenClawApiResponse {
  status: number;
  body: unknown;
}

export interface OpenClawApiRoutes {
  evaluate(body: Record<string, unknown>): OpenClawApiResponse;
  validate(body: Record<string, unknown>): OpenClawApiResponse;
  publishArtifact(body: Record<string, unknown>): OpenClawApiResponse;
  listArtifacts(params: URLSearchParams): OpenClawApiResponse;
  fetchArtifact(artifactId: string): OpenClawApiResponse;
  distillStatus(params: URLSearchParams): OpenClawApiResponse;
  triggerDistillation(body: Record<string, unknown>): OpenClawApiResponse;
  getDistillJob(jobId: string): OpenClawApiResponse;
  updateDistillJob(jobId: string, body: Record<string, unknown>): OpenClawApiResponse;
  capabilities(): OpenClawApiResponse;
  discoveryCapabilities(): OpenClawApiResponse;
  discoveryScenario(scenarioName: string): OpenClawApiResponse;
  discoveryHealth(): OpenClawApiResponse;
  discoveryScenarioArtifacts(scenarioName: string): OpenClawApiResponse;
  skillManifest(): OpenClawApiResponse;
}

export function buildOpenClawApiRoutes(opts: {
  knowledgeRoot: string;
  settings: AppSettings;
  openStore: () => SQLiteStore;
}): OpenClawApiRoutes {
  const service = new OpenClawService(opts);
  return {
    evaluate: (body) => mapErrorToResponse(() => ({ status: 200, body: service.evaluateStrategy(body) })),
    validate: (body) => mapErrorToResponse(() => ({ status: 200, body: service.validateStrategy(body) })),
    publishArtifact: (body) => mapErrorToResponse(() => ({ status: 200, body: service.publishArtifact(body) })),
    listArtifacts: (params) => ({ status: 200, body: service.listArtifacts(params) }),
    fetchArtifact: (artifactId) => mapErrorToResponse(() => {
      const artifact = service.fetchArtifact(artifactId);
      return artifact
        ? { status: 200, body: artifact }
        : { status: 404, body: { detail: `Artifact '${artifactId}' not found` } };
    }),
    distillStatus: (params) => ({ status: 200, body: service.distillStatus(params) }),
    triggerDistillation: (body) => mapErrorToResponse(() => {
      const result = service.triggerDistillation(body);
      return "error" in result
        ? { status: 400, body: result }
        : { status: 200, body: result };
    }),
    getDistillJob: (jobId) => {
      const job = service.getDistillJob(jobId);
      return job
        ? { status: 200, body: job }
        : { status: 404, body: { detail: `Distillation job '${jobId}' not found` } };
    },
    updateDistillJob: (jobId, body) => mapErrorToResponse(() => {
      const job = service.updateDistillJob(jobId, body);
      return job
        ? { status: 200, body: job }
        : { status: 404, body: { detail: `Distillation job '${jobId}' not found` } };
    }),
    capabilities: () => ({ status: 200, body: service.capabilities() }),
    discoveryCapabilities: () => ({ status: 200, body: service.advertiseCapabilities() }),
    discoveryScenario: (scenarioName) => mapErrorToResponse(
      () => ({ status: 200, body: service.discoverScenarioCapabilities(scenarioName) }),
      404,
    ),
    discoveryHealth: () => ({ status: 200, body: service.runtimeHealth() }),
    discoveryScenarioArtifacts: (scenarioName) => ({
      status: 200,
      body: service.scenarioArtifactLookup(scenarioName),
    }),
    skillManifest: () => ({ status: 200, body: service.skillManifest() }),
  };
}

function mapErrorToResponse(fn: () => OpenClawApiResponse, defaultStatus = 400): OpenClawApiResponse {
  try {
    return fn();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      status: error instanceof DistillJobError ? 400 : defaultStatus,
      body: { detail: message },
    };
  }
}
