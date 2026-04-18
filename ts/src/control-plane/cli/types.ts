// Shared CliContext + CliResult types for control-plane subcommand modules.

export interface CliContext {
  /** Working directory (registry root). */
  readonly cwd: string;
  /** Resolve a (possibly relative) path against `cwd`. */
  resolve(p: string): string;
  /** Wall-clock ISO timestamp for new events. Injectable for tests. */
  now(): string;
}

export interface CliResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}
