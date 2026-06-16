import Ajv from "ajv";

import {
  agentAppFetchHostCapabilityManifestSchema,
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
  type AgentAppFetchCatalogPlan,
  type AgentAppFetchHostCapabilityManifest,
  type AgentAppFetchHostCapabilityName,
  type AgentAppFetchRuntimeFactoryPlan,
} from "../src/control-plane/agent-app-fetch/index.js";

export interface FetchHostCapabilityManifestExampleFile {
  path: string;
  contents: string;
}

export interface FetchHostCapabilityManifestExampleArtifacts {
  catalogPlan: AgentAppFetchCatalogPlan;
  runtimeFactoryPlan: AgentAppFetchRuntimeFactoryPlan;
  files: FetchHostCapabilityManifestExampleFile[];
  manifest: AgentAppFetchHostCapabilityManifest;
  hostCapabilityKeys: AgentAppFetchHostCapabilityName[];
  runtimeFactoryNames: string[];
  validateManifest(candidate: unknown): boolean;
  validationErrors(): string[];
}

export function buildFetchHostCapabilityManifestExampleArtifacts(): FetchHostCapabilityManifestExampleArtifacts {
  const catalogPlan = planAgentAppFetchCatalog({
    entries: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        triggers: { webhook: true },
      },
      {
        name: "audit",
        relativePath: ".autoctx/agents/audit.mjs",
        extension: ".mjs",
      },
    ],
  });
  const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
    entries: [
      {
        name: "standard",
        relativePath: ".autoctx/runtimes/standard.mjs",
        extension: ".mjs",
      },
    ],
  });
  const manifestJson = renderAgentAppFetchHostCapabilityManifest(catalogPlan);
  const schemaJson = renderAgentAppFetchHostCapabilityManifestSchema();
  const manifest = JSON.parse(manifestJson) as AgentAppFetchHostCapabilityManifest;
  const validate = new Ajv({ allErrors: true, strict: true }).compile(
    agentAppFetchHostCapabilityManifestSchema,
  );
  let latestErrors: string[] = [];

  return {
    catalogPlan,
    runtimeFactoryPlan,
    files: [
      {
        path: "agent-app-fetch-host-capability-manifest.json",
        contents: manifestJson,
      },
      {
        path: "agent-app-fetch-host-capability-manifest.schema.json",
        contents: schemaJson,
      },
    ],
    manifest,
    hostCapabilityKeys: [...manifest.acceptedHostCapabilities],
    runtimeFactoryNames: runtimeFactoryPlan.entries.map((entry) => entry.name),
    validateManifest(candidate) {
      const valid = validate(candidate);
      latestErrors = valid
        ? []
        : (validate.errors ?? []).map(
            (error) => `${error.instancePath || "/"} ${error.message ?? "is invalid"}`,
          );
      return valid;
    },
    validationErrors() {
      return [...latestErrors];
    },
  };
}
