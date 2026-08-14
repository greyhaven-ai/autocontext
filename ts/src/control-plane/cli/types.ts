// Shared CliContext + CliResult types for control-plane subcommand modules.

import type {
  RegistryRuntimeActivationResult,
  RegistryRuntimePromotionRequest,
  RegistryRuntimeRollbackRequest,
} from "../activation/registry-controller.js";

export interface CliRuntimeActivation {
  promote(request: RegistryRuntimePromotionRequest): Promise<RegistryRuntimeActivationResult>;
  rollback(request: RegistryRuntimeRollbackRequest): Promise<RegistryRuntimeActivationResult>;
}

export interface CliContext {
  /** Working directory (registry root). */
  readonly cwd: string;
  /** Resolve a (possibly relative) path against `cwd`. */
  resolve(p: string): string;
  /** Wall-clock ISO timestamp for new events. Injectable for tests. */
  now(): string;
  /** Optional host-owned live-runtime transaction integration. */
  readonly runtimeActivation?: CliRuntimeActivation;
}

export interface CliResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}
