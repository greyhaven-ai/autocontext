import { Buffer } from "node:buffer";

export const BLOB_HELP_TEXT = `autoctx blob — Manage blob store for large artifacts

Subcommands:
  sync       Sync a run's artifacts to the blob store
  status     Show blob store status (total blobs, bytes, synced runs)
  hydrate    Download a remote blob into the local cache

Examples:
  autoctx blob status --json
  autoctx blob sync --run-id run_001 --json
  autoctx blob hydrate --key runs/run_001/events.ndjson

Requires AUTOCONTEXT_BLOB_STORE_ENABLED=true`;

export function getBlobSubcommand(
  subcommand: string | undefined,
): { kind: "help" } | { kind: "command"; subcommand: string } {
  if (!subcommand || subcommand === "--help" || subcommand === "-h") {
    return { kind: "help" };
  }
  return { kind: "command", subcommand };
}

export function renderBlobStatusResult(
  result: { totalBlobs: number; totalBytes: number; runCount: number; syncedRuns: string[] },
  json: boolean,
): string {
  if (json) {
    return JSON.stringify(result, null, 2);
  }
  return [
    `Blob store: ${result.totalBlobs} blobs, ${result.totalBytes} bytes`,
    `Synced runs: ${result.runCount} (${result.syncedRuns.join(", ") || "none"})`,
  ].join("\n");
}

export function executeBlobStatusWorkflow(opts: {
  json: boolean;
  createSyncManager: () => {
    status(): { totalBlobs: number; totalBytes: number; runCount: number; syncedRuns: string[] };
  };
}): string {
  return renderBlobStatusResult(opts.createSyncManager().status(), opts.json);
}

export function renderBlobSyncResult(
  result: { syncedCount: number; totalBytes: number; skippedCount: number; errors: string[] },
  json: boolean,
): { stdout: string; stderrLines?: string[] } {
  if (json) {
    return { stdout: JSON.stringify(result, null, 2) };
  }
  return {
    stdout: `Synced ${result.syncedCount} artifacts (${result.totalBytes} bytes), skipped ${result.skippedCount}`,
    stderrLines: result.errors.map((error) => `  Error: ${error}`),
  };
}

export function executeBlobSyncWorkflow(opts: {
  runId: string | undefined;
  json: boolean;
  createSyncManager: () => {
    syncRun(runId: string): { syncedCount: number; totalBytes: number; skippedCount: number; errors: string[] };
  };
}): { stdout: string; stderrLines?: string[] } {
  if (!opts.runId) {
    throw new Error("Usage: autoctx blob sync --run-id <run-id> [--json]");
  }
  return renderBlobSyncResult(opts.createSyncManager().syncRun(opts.runId), opts.json);
}

export function executeBlobHydrateWorkflow(opts: {
  key: string | undefined;
  output: string | undefined;
  store: { get(key: string): Buffer | null };
  writeOutputFile?: (outputPath: string, data: Buffer) => void;
}): { stdout?: string; stdoutBuffer?: Buffer } {
  if (!opts.key) {
    throw new Error("Usage: autoctx blob hydrate --key <blob-key> [-o <output-path>]");
  }
  const data = opts.store.get(opts.key);
  if (!data) {
    throw new Error(`Blob not found: ${opts.key}`);
  }
  if (!opts.output) {
    return { stdoutBuffer: data };
  }
  opts.writeOutputFile?.(opts.output, data);
  return {
    stdout: `Hydrated ${opts.key} → ${opts.output} (${data.length} bytes)`,
  };
}
