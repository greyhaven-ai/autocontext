import { describe, expect, it, vi } from "vitest";

import {
  BLOB_HELP_TEXT,
  executeBlobHydrateWorkflow,
  executeBlobStatusWorkflow,
  executeBlobSyncWorkflow,
  getBlobSubcommand,
  renderBlobStatusResult,
  renderBlobSyncResult,
} from "../src/cli/blob-command-workflow.js";

describe("blob command workflow", () => {
  it("exposes stable help text", () => {
    expect(BLOB_HELP_TEXT).toContain("autoctx blob");
    expect(BLOB_HELP_TEXT).toContain("sync");
    expect(BLOB_HELP_TEXT).toContain("status");
    expect(BLOB_HELP_TEXT).toContain("hydrate");
  });

  it("detects help/no-subcommand cases", () => {
    expect(getBlobSubcommand(undefined)).toEqual({ kind: "help" });
    expect(getBlobSubcommand("--help")).toEqual({ kind: "help" });
    expect(getBlobSubcommand("-h")).toEqual({ kind: "help" });
    expect(getBlobSubcommand("status")).toEqual({ kind: "command", subcommand: "status" });
  });

  it("renders blob status results in json and human forms", () => {
    const result = { totalBlobs: 3, totalBytes: 1200, runCount: 2, syncedRuns: ["r1", "r2"] };
    expect(renderBlobStatusResult(result, true)).toBe(JSON.stringify(result, null, 2));
    expect(renderBlobStatusResult(result, false)).toBe(
      [
        "Blob store: 3 blobs, 1200 bytes",
        "Synced runs: 2 (r1, r2)",
      ].join("\n"),
    );
  });

  it("executes blob status workflow", () => {
    const status = vi.fn(() => ({ totalBlobs: 0, totalBytes: 0, runCount: 0, syncedRuns: [] }));
    const output = executeBlobStatusWorkflow({
      json: false,
      createSyncManager: () => ({ status }),
    });
    expect(status).toHaveBeenCalled();
    expect(output).toBe("Blob store: 0 blobs, 0 bytes\nSynced runs: 0 (none)");
  });

  it("requires run-id for blob sync", () => {
    expect(() =>
      executeBlobSyncWorkflow({
        runId: undefined,
        json: false,
        createSyncManager: () => ({ syncRun: vi.fn() }),
      }),
    ).toThrow("Usage: autoctx blob sync --run-id <run-id> [--json]");
  });

  it("renders blob sync results in json and human forms", () => {
    const result = { syncedCount: 2, totalBytes: 512, skippedCount: 1, errors: ["oops"] };
    expect(renderBlobSyncResult(result, true)).toEqual({ stdout: JSON.stringify(result, null, 2) });
    expect(renderBlobSyncResult(result, false)).toEqual({
      stdout: "Synced 2 artifacts (512 bytes), skipped 1",
      stderrLines: ["  Error: oops"],
    });
  });

  it("executes blob sync workflow", () => {
    const syncRun = vi.fn(() => ({ syncedCount: 1, totalBytes: 100, skippedCount: 0, errors: [] }));
    const result = executeBlobSyncWorkflow({
      runId: "run_001",
      json: false,
      createSyncManager: () => ({ syncRun }),
    });
    expect(syncRun).toHaveBeenCalledWith("run_001");
    expect(result).toEqual({
      stdout: "Synced 1 artifacts (100 bytes), skipped 0",
      stderrLines: [],
    });
  });

  it("requires a key for hydrate", () => {
    expect(() =>
      executeBlobHydrateWorkflow({
        key: undefined,
        output: undefined,
        store: { get: vi.fn() },
      }),
    ).toThrow("Usage: autoctx blob hydrate --key <blob-key> [-o <output-path>]");
  });

  it("errors when blob data is missing", () => {
    expect(() =>
      executeBlobHydrateWorkflow({
        key: "runs/r1/blob.bin",
        output: undefined,
        store: { get: () => null },
      }),
    ).toThrow("Blob not found: runs/r1/blob.bin");
  });

  it("hydrates to stdout or file output", () => {
    const fileWrite = vi.fn();
    const data = Buffer.from("hello");

    expect(
      executeBlobHydrateWorkflow({
        key: "runs/r1/blob.bin",
        output: undefined,
        store: { get: () => data },
      }),
    ).toEqual({ stdoutBuffer: data });

    expect(
      executeBlobHydrateWorkflow({
        key: "runs/r1/blob.bin",
        output: "/tmp/blob.bin",
        store: { get: () => data },
        writeOutputFile: fileWrite,
      }),
    ).toEqual({ stdout: "Hydrated runs/r1/blob.bin → /tmp/blob.bin (5 bytes)" });
    expect(fileWrite).toHaveBeenCalledWith("/tmp/blob.bin", data);
  });
});
