import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TraceBatch } from "../../../src/production-traces/sdk/trace-batch.js";
import { buildTrace } from "../../../src/production-traces/sdk/build-trace.js";
import type { AppId, EnvironmentTag } from "../../../src/production-traces/contract/branded-ids.js";

function makeTrace(traceIdSuffix: string) {
  return buildTrace({
    provider: "openai",
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" }],
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 1, tokensOut: 1 },
    env: { environmentTag: "production" as EnvironmentTag, appId: "my-app" as AppId },
    traceId: `01HZ6X2K7M9A3B4C5D6E7F8G${traceIdSuffix}`,
  });
}

describe("TraceBatch", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autoctx-trace-batch-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("length is 0 for an empty batch", () => {
    const batch = new TraceBatch();
    expect(batch.length).toBe(0);
  });

  test("length increases with add", () => {
    const batch = new TraceBatch();
    batch.add(makeTrace("9A"));
    batch.add(makeTrace("9B"));
    expect(batch.length).toBe(2);
  });

  test("flush on an empty batch returns null without touching disk", () => {
    const batch = new TraceBatch();
    const path = batch.flush({ cwd: dir });
    expect(path).toBeNull();
    expect(existsSync(join(dir, ".autocontext"))).toBe(false);
  });

  test("flush writes accumulated traces and resets the batch", () => {
    const batch = new TraceBatch();
    batch.add(makeTrace("9A"));
    batch.add(makeTrace("9B"));
    const path = batch.flush({ cwd: dir });
    expect(typeof path).toBe("string");
    expect(path).not.toBeNull();
    expect(batch.length).toBe(0);
    const contents = readFileSync(path as string, "utf-8");
    const lines = contents.split("\n").filter((l) => l.length > 0);
    expect(lines).toHaveLength(2);
  });

  test("clear() empties the batch without writing to disk", () => {
    const batch = new TraceBatch();
    batch.add(makeTrace("9A"));
    batch.clear();
    expect(batch.length).toBe(0);
    expect(existsSync(join(dir, ".autocontext"))).toBe(false);
  });

  test("flush after flush is a safe no-op (returns null, doesn't write empty file)", () => {
    const batch = new TraceBatch();
    batch.add(makeTrace("9A"));
    batch.flush({ cwd: dir });
    const secondPath = batch.flush({ cwd: dir });
    expect(secondPath).toBeNull();
  });
});
