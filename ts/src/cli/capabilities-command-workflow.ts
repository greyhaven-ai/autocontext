import type { Capabilities } from "../mcp/capabilities.js";
import { visibleSupportedCommandNames } from "./command-registry.js";

export const CAPABILITIES_COMMANDS: readonly string[] = visibleSupportedCommandNames();

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
