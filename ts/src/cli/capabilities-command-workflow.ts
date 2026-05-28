import { existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import type { Capabilities } from "../mcp/capabilities.js";
import { loadContract } from "./cli-contract.js";
import type { CommandSpec, Contract } from "./cli-contract.js";
import { visibleSupportedCommandNames } from "./command-registry.js";

export const CAPABILITIES_COMMANDS: readonly string[] = visibleSupportedCommandNames();

/**
 * AC-697 slice 5: capabilities now loads `docs/cli-contract.json` so
 * the JSON payload advertises the canonical commands, their aliases,
 * and per-runtime support exactly once across both runtimes. The
 * legacy `commands: string[]` field is preserved for backward
 * compatibility; new consumers should read `contract.commands` which
 * carries the full canonical surface.
 */
export interface CapabilitiesContractCommand {
  readonly id: string;
  readonly path: readonly string[];
  readonly summary: string;
  readonly audience: string;
  readonly maturity: string;
  readonly aliases: readonly string[];
  readonly runtime_support: {
    readonly python: { readonly status: string; readonly reason?: string };
    readonly typescript: { readonly status: string; readonly reason?: string };
  };
}

export interface CapabilitiesContractPayload {
  readonly schema_version: number;
  readonly commands: readonly CapabilitiesContractCommand[];
}

export interface CapabilitiesCommandPayload extends Omit<Capabilities, "features"> {
  commands: string[];
  features: {
    mcp_server: boolean;
    training_export: boolean;
    custom_scenarios: boolean;
    interactive_server: boolean;
    playbook_versioning: boolean;
  };
  project_config: Record<string, unknown> | null;
  contract: CapabilitiesContractPayload;
}

function _defaultContractPath(): string {
  // PR #1000 review (P2): the previous design walked three levels up
  // from `dist/cli/file.js` to reach `<repo>/docs/cli-contract.json`,
  // which works in the monorepo but lands outside the package
  // directory in the installed npm tarball. The `build:cli-contract`
  // script (chained into `npm run build`) copies the contract source
  // into `dist/cli-contract.json` at build time, so the runtime
  // loader resolves it relative to `dist/cli/file.js` -> `..` ->
  // `dist/cli-contract.json`. The dev tree (tsx-from-source from
  // `src/cli/`) falls back to the repo-relative path.
  const here = dirname(fileURLToPath(import.meta.url));
  const distCopy = resolve(here, "..", "cli-contract.json");
  if (existsSync(distCopy)) {
    return distCopy;
  }
  // Dev tree fallback: `src/cli/` -> `..` -> `..` -> `..` -> repo root.
  return resolve(here, "..", "..", "..", "docs", "cli-contract.json");
}

function _projectContract(path?: string): Contract {
  return loadContract(path ?? _defaultContractPath());
}

function _toContractCommand(cmd: CommandSpec): CapabilitiesContractCommand {
  return {
    id: cmd.id,
    path: cmd.path,
    summary: cmd.summary,
    audience: cmd.audience,
    maturity: cmd.maturity,
    aliases: cmd.aliases,
    runtime_support: {
      python: {
        status: cmd.runtime_support.python.status,
        ...(cmd.runtime_support.python.reason ? { reason: cmd.runtime_support.python.reason } : {}),
      },
      typescript: {
        status: cmd.runtime_support.typescript.status,
        ...(cmd.runtime_support.typescript.reason
          ? { reason: cmd.runtime_support.typescript.reason }
          : {}),
      },
    },
  };
}

export function buildCapabilitiesPayload(
  baseCapabilities: Capabilities,
  projectConfig: Record<string, unknown> | null,
  options: { readonly contractPath?: string } = {},
): CapabilitiesCommandPayload {
  const { features: _baseFeatures, ...rest } = baseCapabilities;
  const contract = _projectContract(options.contractPath);
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
    contract: {
      schema_version: contract.schema_version,
      commands: contract.commands.map(_toContractCommand),
    },
  };
}
