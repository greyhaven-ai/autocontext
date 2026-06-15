import { AGENT_APP_FETCH_ROUTES } from "./catalog-planner.js";
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

export const AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES = [
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
] as const satisfies readonly AgentAppFetchHostCapabilityName[];

export const AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS = [
  "runtime_filesystem_discovery",
  "ambient_environment_capture",
  "local_shell_execution",
  "provider_deployment_configuration",
  "hosted_orchestration",
] as const satisfies readonly AgentAppFetchUnsupportedDefault[];

export const agentAppFetchHostCapabilityManifestSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  $id: "https://autocontext.dev/schemas/agent-app-fetch-host-capability-manifest.schema.json",
  title: "AutoContext Fetch host capability manifest",
  type: "object",
  additionalProperties: false,
  required: [
    "target",
    "routes",
    "agents",
    "acceptedHostCapabilities",
    "requiredHostCapabilities",
    "unsupportedDefaults",
  ],
  properties: {
    target: { const: "fetch" },
    routes: {
      type: "array",
      items: { enum: [...AGENT_APP_FETCH_ROUTES] },
      minItems: AGENT_APP_FETCH_ROUTES.length,
      maxItems: AGENT_APP_FETCH_ROUTES.length,
      uniqueItems: true,
    },
    agents: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["name", "relativePath", "extension"],
        properties: {
          name: { type: "string", pattern: "^[A-Za-z0-9._-]+$" },
          relativePath: {
            type: "string",
            pattern: "^(?!/)(?!.*\\\\)(?!.*(?:^|/)\\.\\.(?:/|$)).+$",
          },
          extension: { type: "string", pattern: "^\\.[^/\\\\]+$" },
          triggers: { type: "object" },
        },
      },
    },
    acceptedHostCapabilities: {
      type: "array",
      items: { enum: [...AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES] },
      minItems: AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES.length,
      maxItems: AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES.length,
      uniqueItems: true,
    },
    requiredHostCapabilities: {
      type: "array",
      items: { enum: [...AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES] },
      minItems: 0,
      maxItems: 0,
      uniqueItems: true,
    },
    unsupportedDefaults: {
      type: "array",
      items: { enum: [...AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS] },
      minItems: AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS.length,
      maxItems: AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS.length,
      uniqueItems: true,
    },
  },
} as const;

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
    acceptedHostCapabilities: [...AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES],
    requiredHostCapabilities: [],
    unsupportedDefaults: [...AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS],
  };
}

export function renderAgentAppFetchHostCapabilityManifest(plan: AgentAppFetchCatalogPlan): string {
  return `${JSON.stringify(createAgentAppFetchHostCapabilityManifest(plan), null, 2)}\n`;
}

export function renderAgentAppFetchHostCapabilityManifestSchema(): string {
  return `${JSON.stringify(agentAppFetchHostCapabilityManifestSchema, null, 2)}\n`;
}

function cloneRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
