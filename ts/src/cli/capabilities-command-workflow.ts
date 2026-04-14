import type { Capabilities } from "../mcp/capabilities.js";

export const CAPABILITIES_COMMANDS = [
  "init",
  "run",
  "list",
  "replay",
  "benchmark",
  "export",
  "export-training-data",
  "import-package",
  "new-scenario",
  "capabilities",
  "login",
  "whoami",
  "logout",
  "providers",
  "models",
  "mission",
  "campaign",
  "tui",
  "judge",
  "improve",
  "repl",
  "queue",
  "status",
  "serve",
  "mcp-serve",
  "version",
] as const;

export interface CapabilitiesCommandPayload
  extends Omit<Capabilities, "features"> {
  commands: string[];
  features: {
    mcp_server: boolean;
    training_export: boolean;
    custom_scenarios: boolean;
    interactive_server: boolean;
    playbook_versioning: boolean;
  };
  project_config: Record<string, unknown> | null;
}

export function buildCapabilitiesPayload(
  baseCapabilities: Capabilities,
  projectConfig: Record<string, unknown> | null,
): CapabilitiesCommandPayload {
  const { features: _baseFeatures, ...rest } = baseCapabilities;
  return {
    ...rest,
    commands: [...CAPABILITIES_COMMANDS],
    features: {
      mcp_server: true,
      training_export: true,
      custom_scenarios: true,
      interactive_server: true,
      playbook_versioning: true,
    },
    project_config: projectConfig,
  };
}
