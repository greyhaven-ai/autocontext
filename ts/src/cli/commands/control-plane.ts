/**
 * Control-plane namespace commands: `candidate`/`eval`/`promotion`/`registry`/
 * `emit-pr` (via `cmdControlPlane`), plus `production-traces`, `instrument`,
 * `trace-findings`, `probes` (AC-853 split of command-handlers.ts).
 */
import type { ControlPlaneCommandName } from "../command-registry.js";

// ---------------------------------------------------------------------------
// Control-plane commands (Layer 8 — candidate / eval / promotion / registry)
// ---------------------------------------------------------------------------

export async function cmdControlPlane(topCommand: ControlPlaneCommandName): Promise<void> {
  const { runControlPlaneCommand } = await import("../../control-plane/cli/index.js");
  const subArgs = process.argv.slice(3);
  const result = await runControlPlaneCommand([topCommand, ...subArgs]);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

// ---------------------------------------------------------------------------
// Production-traces namespace (Foundation A / Layer 7 — AC-539)
// ---------------------------------------------------------------------------

export async function cmdProductionTraces(): Promise<void> {
  const { runProductionTracesCommand } = await import("../../production-traces/cli/index.js");
  const subArgs = process.argv.slice(3);
  const result = await runProductionTracesCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

// Instrument namespace (A2-I / Layer 7 — AC-540)

export async function cmdInstrument(): Promise<void> {
  const { runInstrumentCommand } = await import("../../control-plane/instrument/cli/index.js");
  const subArgs = process.argv.slice(3);
  const result = await runInstrumentCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

export async function cmdTraceFindings(): Promise<void> {
  const { runTraceFindingsCommand } = await import("../trace-findings-command-workflow.js");
  const subArgs = process.argv.slice(3);
  const result = await runTraceFindingsCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

export async function cmdProbes(): Promise<void> {
  const { runProbesCommand } = await import("../../control-plane/contract-probes/cli/index.js");
  const subArgs = process.argv.slice(3);
  const result = await runProbesCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}
