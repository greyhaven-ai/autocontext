import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

afterEach(() => {
  vi.doUnmock("node:fs");
  vi.resetModules();
});

describe("runtime snapshot", () => {
  it("returns truncated output previews without full content payloads", async () => {
    const { collectRuntimeSnapshot, parseRuntimeSnapshotRequest } = await import("../src/runtime-snapshot.js");
    const fullContent = "x".repeat(700);
    const request = parseRuntimeSnapshotRequest({
      run_id: "run-1",
      include_outputs: true,
      generation_index: 1,
    });

    const snapshot = collectRuntimeSnapshot(
      {},
      {
        getRun: () => ({ run_id: "run-1", status: "completed" }),
        getGenerations: () => [{ run_id: "run-1", generation_index: 1, best_score: 0.91 }],
        getAgentOutputs: () => [{ run_id: "run-1", generation_index: 1, role: "competitor", content: fullContent }],
      },
      { eventStreamPath: "/missing/events.ndjson" },
      request,
    );

    const outputs = snapshot.agentOutputs as Array<Record<string, unknown>>;
    expect(outputs).toHaveLength(1);
    expect(outputs[0]).toEqual(expect.objectContaining({
      role: "competitor",
      contentLength: 700,
      preview: "x".repeat(500),
    }));
    expect(outputs[0]).not.toHaveProperty("content");
  });

  it("tails a bounded event-stream byte range instead of reading the whole stream", async () => {
    const stream = Buffer.from(
      [
        JSON.stringify({ event: "run_started", payload: { run_id: "old" } }),
        JSON.stringify({ event: "run_started", payload: { run_id: "run-1" } }),
        JSON.stringify({ event: "generation_completed", payload: { run_id: "run-1", generation_index: 1 } }),
      ].join("\n") + "\n",
      "utf-8",
    );
    const readLengths: number[] = [];
    const closeSync = vi.fn();

    vi.doMock("node:fs", () => ({
      closeSync,
      existsSync: () => true,
      openSync: () => 12,
      readFileSync: () => {
        throw new Error("event snapshots must not read the entire stream");
      },
      readSync: (_fd: number, buffer: Buffer, offset: number, length: number, position: number) => {
        readLengths.push(length);
        const chunk = stream.subarray(position, position + length);
        chunk.copy(buffer, offset);
        return chunk.length;
      },
      statSync: () => ({ size: stream.length }),
    }));

    const { collectRuntimeSnapshot, parseRuntimeSnapshotRequest } = await import("../src/runtime-snapshot.js");
    const snapshot = collectRuntimeSnapshot(
      {},
      {
        getRun: () => ({ run_id: "run-1", status: "completed" }),
        getGenerations: () => [],
      },
      { eventStreamPath: "/events.ndjson" },
      parseRuntimeSnapshotRequest({ run_id: "run-1", limit: 2 }),
    );

    expect(readLengths.length).toBeGreaterThan(0);
    expect(Math.max(...readLengths)).toBeLessThanOrEqual(64 * 1024);
    expect(closeSync).toHaveBeenCalledWith(12);
    expect(snapshot.events).toEqual([
      expect.objectContaining({ event: "run_started" }),
      expect.objectContaining({ event: "generation_completed" }),
    ]);
  });

  it("includes recent compaction ledger entries for selected runs", async () => {
    const { collectRuntimeSnapshot, parseRuntimeSnapshotRequest, renderRuntimeSnapshot } = await import("../src/runtime-snapshot.js");
    const root = mkdtempSync(join(tmpdir(), "autoctx-compactions-"));
    try {
      mkdirSync(join(root, "run-1"), { recursive: true });
      writeFileSync(
        join(root, "run-1", "compactions.jsonl"),
        [
          JSON.stringify({ type: "compaction", id: "a", summary: "old", firstKeptEntryId: "component:playbook:kept", tokensBefore: 100 }),
          JSON.stringify({ type: "compaction", id: "b", summary: "new", firstKeptEntryId: "component:experiment_log:kept", tokensBefore: 200 }),
        ].join("\n") + "\n",
        "utf-8",
      );

      const snapshot = collectRuntimeSnapshot(
        {},
        {
          getRun: () => ({ run_id: "run-1", status: "completed" }),
          getGenerations: () => [],
        },
        { runsRoot: root, eventStreamPath: "/missing/events.ndjson" },
        parseRuntimeSnapshotRequest({ run_id: "run-1", limit: 1 }),
      );

      expect(snapshot.compactions).toEqual([
        expect.objectContaining({ id: "b", firstKeptEntryId: "component:experiment_log:kept" }),
      ]);
      expect(renderRuntimeSnapshot(snapshot)).toContain("Compactions: 1");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("keeps compaction ledger reads contained under runsRoot", async () => {
    const { collectRuntimeSnapshot, parseRuntimeSnapshotRequest } = await import("../src/runtime-snapshot.js");
    const base = mkdtempSync(join(tmpdir(), "autoctx-compaction-containment-"));
    const root = join(base, "runs");
    const outside = join(base, "outside");
    try {
      mkdirSync(outside, { recursive: true });
      writeFileSync(
        join(outside, "compactions.jsonl"),
        JSON.stringify({ type: "compaction", id: "escape", summary: "outside" }) + "\n",
        "utf-8",
      );

      const snapshot = collectRuntimeSnapshot(
        {},
        {
          getRun: () => ({ run_id: "../outside", status: "completed" }),
          getGenerations: () => [],
        },
        { runsRoot: root, eventStreamPath: "/missing/events.ndjson" },
        parseRuntimeSnapshotRequest({ run_id: "../outside", limit: 1 }),
      );

      expect(snapshot.compactions).toEqual([]);
    } finally {
      rmSync(base, { recursive: true, force: true });
    }
  });
});
