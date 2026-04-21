import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync, existsSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { writeJsonl } from "../../../src/production-traces/sdk/write-jsonl.js";
import { buildTrace } from "../../../src/production-traces/sdk/build-trace.js";
import type { AppId, EnvironmentTag } from "../../../src/production-traces/contract/branded-ids.js";
import { canonicalJsonStringify } from "../../../src/control-plane/contract/canonical-json.js";

function traceAt(startedAt: string, suffix: string) {
  return buildTrace({
    provider: "openai",
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: "hi", timestamp: startedAt }],
    timing: { startedAt, endedAt: startedAt, latencyMs: 0 },
    usage: { tokensIn: 1, tokensOut: 1 },
    env: { environmentTag: "production" as EnvironmentTag, appId: "my-app" as AppId },
    traceId: `01HZ6X2K7M9A3B4C5D6E7F8G${suffix}`,
  });
}

describe("writeJsonl", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autoctx-writejsonl-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
    delete process.env.AUTOCONTEXT_REGISTRY_PATH;
  });

  test("empty array returns null (mirrors Python no-op)", () => {
    const result = writeJsonl([], { cwd: dir });
    expect(result).toBeNull();
    expect(existsSync(join(dir, ".autocontext"))).toBe(false);
  });

  test("single trace writes one line to the incoming partition", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl(trace, { cwd: dir }) as string;
    expect(path).not.toBeNull();
    expect(path).toContain(join(".autocontext", "production-traces", "incoming", "2026-04-17"));
    expect(path.endsWith(".jsonl")).toBe(true);
    const contents = readFileSync(path, "utf-8");
    const lines = contents.split("\n").filter((l) => l.length > 0);
    expect(lines).toHaveLength(1);
  });

  test("path shape matches spec: <cwd>/.autocontext/production-traces/incoming/<YYYY-MM-DD>/<batch-ulid>.jsonl", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl([trace], { cwd: dir, batchId: "01HZ6X2K7M9A3B4C5D6E7F8GHH" }) as string;
    const expected = join(dir, ".autocontext", "production-traces", "incoming", "2026-04-17", "01HZ6X2K7M9A3B4C5D6E7F8GHH.jsonl");
    expect(path).toBe(expected);
  });

  test("date partition is derived from first trace's timing.startedAt in UTC", () => {
    // 2026-04-17T23:30:00Z stays on 2026-04-17 regardless of local tz.
    const trace = traceAt("2026-04-17T23:30:00.000Z", "9A");
    const path = writeJsonl(trace, { cwd: dir }) as string;
    expect(path).toContain(`${sep}2026-04-17${sep}`);
  });

  test("each line is canonical JSON (byte-deterministic)", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9B");
    const path = writeJsonl(trace, { cwd: dir }) as string;
    const line = readFileSync(path, "utf-8").split("\n")[0];
    expect(line).toBe(canonicalJsonStringify(trace));
  });

  test("multiple traces write one line per trace in order", () => {
    const a = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const b = traceAt("2026-04-17T12:00:00.000Z", "9B");
    const c = traceAt("2026-04-17T12:00:00.000Z", "9C");
    const path = writeJsonl([a, b, c], { cwd: dir }) as string;
    const lines = readFileSync(path, "utf-8").split("\n").filter((l) => l.length > 0);
    expect(lines).toEqual([canonicalJsonStringify(a), canonicalJsonStringify(b), canonicalJsonStringify(c)]);
  });

  test("AUTOCONTEXT_REGISTRY_PATH env var resolves when cwd option absent", () => {
    process.env.AUTOCONTEXT_REGISTRY_PATH = dir;
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl(trace) as string;
    expect(path.startsWith(dir)).toBe(true);
  });

  test("returns an absolute path", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl(trace, { cwd: dir }) as string;
    expect(path.startsWith(sep)).toBe(true);
  });

  test("each call uses a fresh batch-ulid filename", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const p1 = writeJsonl(trace, { cwd: dir }) as string;
    const p2 = writeJsonl(trace, { cwd: dir }) as string;
    expect(p1).not.toBe(p2);
  });

  test("explicit batchId wins over auto-generated ULID", () => {
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl(trace, { cwd: dir, batchId: "my-batch-id" }) as string;
    expect(path.endsWith("my-batch-id.jsonl")).toBe(true);
  });

  test("creates parent directories as needed", () => {
    const nested = join(dir, "deeply", "nested");
    const trace = traceAt("2026-04-17T12:00:00.000Z", "9A");
    const path = writeJsonl(trace, { cwd: nested }) as string;
    expect(existsSync(path)).toBe(true);
    const stats = statSync(path);
    expect(stats.isFile()).toBe(true);
  });
});
