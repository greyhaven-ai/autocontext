import type { AgentAppFetchCatalogPlan, AgentAppFetchRoute } from "./catalog-planner.js";

export type AgentAppFetchHostCapabilityName =
  | "env"
  | "runtime"
  | "runtimeFactory"
  | "runtimeFactoryName"
  | "workspace"
  | "workspaceStore"
  | "commands"
  | "tools"
  | "eventStore"
  | "sessionEventStore"
  | "eventSink"
  | "maxBodyBytes";

export type AgentAppFetchUnsupportedDefault =
  | "runtime_filesystem_discovery"
  | "ambient_environment_capture"
  | "local_shell_execution"
  | "provider_deployment_configuration"
  | "hosted_orchestration";

export interface AgentAppFetchHostCapabilityManifestAgent {
  name: string;
  relativePath: string;
  extension: string;
  triggers?: Record<string, unknown>;
}

export interface AgentAppFetchHostCapabilityManifest {
  target: AgentAppFetchCatalogPlan["target"];
  routes: AgentAppFetchRoute[];
  agents: AgentAppFetchHostCapabilityManifestAgent[];
  acceptedHostCapabilities: AgentAppFetchHostCapabilityName[];
  requiredHostCapabilities: AgentAppFetchHostCapabilityName[];
  unsupportedDefaults: AgentAppFetchUnsupportedDefault[];
}

const ACCEPTED_HOST_CAPABILITIES: readonly AgentAppFetchHostCapabilityName[] = [
  "env",
  "runtime",
  "runtimeFactory",
  "runtimeFactoryName",
  "workspace",
  "workspaceStore",
  "commands",
  "tools",
  "eventStore",
  "sessionEventStore",
  "eventSink",
  "maxBodyBytes",
];

const UNSUPPORTED_DEFAULTS: readonly AgentAppFetchUnsupportedDefault[] = [
  "runtime_filesystem_discovery",
  "ambient_environment_capture",
  "local_shell_execution",
  "provider_deployment_configuration",
  "hosted_orchestration",
];

export function createAgentAppFetchHostCapabilityManifest(
  plan: AgentAppFetchCatalogPlan,
): AgentAppFetchHostCapabilityManifest {
  return {
    target: plan.target,
    routes: [...plan.routes],
    agents: plan.entries.map((entry) => {
      const agent: AgentAppFetchHostCapabilityManifestAgent = {
        name: entry.name,
        relativePath: entry.relativePath,
        extension: entry.extension,
      };
      const triggers = cloneRecord(entry.triggers);
      if (triggers) agent.triggers = triggers;
      return agent;
    }),
    acceptedHostCapabilities: [...ACCEPTED_HOST_CAPABILITIES],
    requiredHostCapabilities: [],
    unsupportedDefaults: [...UNSUPPORTED_DEFAULTS],
  };
}

export function renderAgentAppFetchHostCapabilityManifest(plan: AgentAppFetchCatalogPlan): string {
  return `${JSON.stringify(createAgentAppFetchHostCapabilityManifest(plan), null, 2)}\n`;
}

function cloneRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
