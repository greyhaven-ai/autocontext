import { afterEach, describe, expect, it, vi } from "vitest";

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
});
