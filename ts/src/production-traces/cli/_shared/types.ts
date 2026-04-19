// Shared CliContext + CliResult types for production-traces CLI commands.
//
// Mirrors Foundation B's `control-plane/cli/types.ts` shape so the two CLIs
// stay drift-friendly. We do not re-export the control-plane types directly
// because the module boundary is cleaner and lets the two layers diverge
// without rippling.

export interface CliContext {
  /** Working directory (project root containing `.autocontext/`). */
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
