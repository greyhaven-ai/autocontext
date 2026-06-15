import {
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchEntrypointTemplate,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
} from "../src/control-plane/agent-app-fetch/index.js";

export interface GeneratedFetchPackageFile {
  path: string;
  contents: string;
}

export interface GeneratedFetchPackageArtifacts {
  files: GeneratedFetchPackageFile[];
  hostCapabilities: string[];
}

export function buildGeneratedFetchPackageArtifacts(): GeneratedFetchPackageArtifacts {
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

  return {
    files: [
      {
        path: "agent-app-fetch-entrypoint.mjs",
        contents: renderAgentAppFetchEntrypointTemplate(catalogPlan, { runtimeFactoryPlan }),
      },
      {
        path: "agent-app-fetch-host-capability-manifest.json",
        contents: renderAgentAppFetchHostCapabilityManifest(catalogPlan),
      },
      {
        path: "agent-app-fetch-host-capability-manifest.schema.json",
        contents: renderAgentAppFetchHostCapabilityManifestSchema(),
      },
    ],
    hostCapabilities: ["env", "runtimeFactoryName", "workspaceStore", "sessionEventStore"],
  };
}
