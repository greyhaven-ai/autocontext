import {
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchEntrypointTemplate,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
  type AgentAppFetchCatalogSourceEntry,
  type AgentAppFetchHostCapabilityName,
  type AgentAppFetchRuntimeFactorySourceEntry,
} from "../src/control-plane/agent-app-fetch/index.js";

export interface GeneratedFetchRuntimeFactoryPackageFile {
  path: string;
  contents: string;
}

export interface GeneratedFetchRuntimeFactoryPackageOptions {
  agentModuleSpecifier?: (entry: AgentAppFetchCatalogSourceEntry) => string;
  runtimeFactoryModuleSpecifier?: (entry: AgentAppFetchRuntimeFactorySourceEntry) => string;
}

export interface GeneratedFetchRuntimeFactoryPackageArtifacts {
  files: GeneratedFetchRuntimeFactoryPackageFile[];
  entrypointSource: string;
  hostCapabilities: AgentAppFetchHostCapabilityName[];
  runtimeFactoryNames: string[];
}

export function buildGeneratedFetchRuntimeFactoryPackageArtifacts(
  options: GeneratedFetchRuntimeFactoryPackageOptions = {},
): GeneratedFetchRuntimeFactoryPackageArtifacts {
  const catalogPlan = planAgentAppFetchCatalog({
    entries: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        triggers: { webhook: true },
      },
    ],
    moduleSpecifier: options.agentModuleSpecifier,
  });
  const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
    entries: [
      {
        name: "standard",
        relativePath: ".autoctx/runtimes/standard.mjs",
        extension: ".mjs",
      },
    ],
    moduleSpecifier: options.runtimeFactoryModuleSpecifier,
  });
  const entrypointSource = renderAgentAppFetchEntrypointTemplate(catalogPlan, {
    runtimeFactoryPlan,
  });
  const manifestJson = renderAgentAppFetchHostCapabilityManifest(catalogPlan);
  const manifest = JSON.parse(manifestJson) as {
    acceptedHostCapabilities: AgentAppFetchHostCapabilityName[];
  };

  return {
    files: [
      {
        path: "agent-app-fetch-entrypoint.mjs",
        contents: entrypointSource,
      },
      {
        path: "agent-app-fetch-host-capability-manifest.json",
        contents: manifestJson,
      },
      {
        path: "agent-app-fetch-host-capability-manifest.schema.json",
        contents: renderAgentAppFetchHostCapabilityManifestSchema(),
      },
      {
        path: "agent-app-fetch-runtime-factory-plan.json",
        contents: `${JSON.stringify(runtimeFactoryPlan, null, 2)}\n`,
      },
    ],
    entrypointSource,
    hostCapabilities: [...manifest.acceptedHostCapabilities],
    runtimeFactoryNames: runtimeFactoryPlan.entries.map((entry) => entry.name),
  };
}
