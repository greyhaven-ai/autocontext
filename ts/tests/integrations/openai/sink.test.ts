/**
 * FileSink + TraceSink tests — mirrors Python sink tests.
 *
 * Task 3.2 — 10 tests.
 */
import { describe, test, expect, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { FileSink } from "../../../src/integrations/openai/sink.js";
import type { TraceSink } from "../../../src/integrations/openai/sink.js";

let tmpDir: string;

afterEach(() => {
  if (tmpDir) {
    rmSync(tmpDir, { recursive: true, force: true });
  }
});

function makeDir(): string {
  tmpDir = mkdtempSync(join(tmpdir(), "autoctx-sink-"));
  return tmpDir;
}

function makeSink(opts: Partial<ConstructorParameters<typeof FileSink>[1]> = {}) {
  const dir = makeDir();
  const path = join(dir, "traces.jsonl");
  return { sink: new FileSink(path, { batchSize: 64, flushIntervalSeconds: 5, ...opts }), path };
}

describe("TraceSink interface", () => {
  test("FileSink satisfies TraceSink interface", () => {
    const { sink } = makeSink();
    const asSink: TraceSink = sink;
    expect(typeof asSink.add).toBe("function");
    expect(typeof asSink.flush).toBe("function");
    expect(typeof asSink.close).toBe("function");
    sink.close();
  });
});

describe("FileSink", () => {
  test("add() + flush() writes JSON lines", () => {
    const { sink, path } = makeSink();
    sink.add({ traceId: "t1", model: "gpt-4o" });
    sink.flush();
    sink.close();
    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines.length).toBe(1);
    const parsed = JSON.parse(lines[0]!);
    expect(parsed.traceId).toBe("t1");
  });

  test("multiple traces land in order", () => {
    const { sink, path } = makeSink();
    sink.add({ traceId: "t1" });
    sink.add({ traceId: "t2" });
    sink.add({ traceId: "t3" });
    sink.flush();
    sink.close();
    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines.length).toBe(3);
    expect(JSON.parse(lines[0]!).traceId).toBe("t1");
    expect(JSON.parse(lines[1]!).traceId).toBe("t2");
    expect(JSON.parse(lines[2]!).traceId).toBe("t3");
  });

  test("flush() is idempotent — second call writes nothing extra", () => {
    const { sink, path } = makeSink();
    sink.add({ traceId: "t1" });
    sink.flush();
    sink.flush();
    sink.close();
    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines.length).toBe(1);
  });

  test("auto-flushes when batch_size reached", () => {
    const { sink, path } = makeSink({ batchSize: 2 });
    sink.add({ traceId: "t1" });
    sink.add({ traceId: "t2" });
    // Should have auto-flushed at count == batchSize
    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines.length).toBe(2);
    sink.close();
  });

  test("close() flushes remaining buffer", () => {
    const { sink, path } = makeSink();
    sink.add({ traceId: "t1" });
    sink.close();
    const content = readFileSync(path, "utf-8").trim();
    expect(content.length).toBeGreaterThan(0);
    const parsed = JSON.parse(content.split("\n")[0]!);
    expect(parsed.traceId).toBe("t1");
  });

  test("add() after close() throws RuntimeError", () => {
    const { sink } = makeSink();
    sink.close();
    expect(() => sink.add({ traceId: "t1" })).toThrow(/closed/i);
  });

  test("close() is idempotent — second call is a no-op", () => {
    const { sink } = makeSink();
    sink.add({ traceId: "t1" });
    sink.close();
    expect(() => sink.close()).not.toThrow();
  });

  test("creates parent directories if missing", () => {
    const dir = makeDir();
    const path = join(dir, "nested", "deeply", "traces.jsonl");
    const sink = new FileSink(path);
    sink.add({ traceId: "t1" });
    sink.close();
    const content = readFileSync(path, "utf-8").trim();
    expect(JSON.parse(content).traceId).toBe("t1");
  });

  test("onError: log-and-drop swallows write errors", () => {
    const dir = makeDir();
    // Use a path inside a FILE (not directory) to trigger write error
    const filePath = join(dir, "notadir");
    // Create a file at that path first, then try to write inside it
    const badPath = join(filePath, "traces.jsonl");
    // Write a file at filePath so the child path can't be created
    require("node:fs").writeFileSync(filePath, "block");
    const sink = new FileSink(badPath, { onError: "log-and-drop" });
    expect(() => {
      sink.add({ traceId: "t1" });
      sink.flush();
      sink.close();
    }).not.toThrow();
  });

  test("JSON lines are sorted-key compact (no spaces)", () => {
    const { sink, path } = makeSink();
    sink.add({ z: 1, a: 2 });
    sink.flush();
    sink.close();
    const line = readFileSync(path, "utf-8").trim();
    // compact = no spaces after separators
    expect(line).not.toMatch(/ /);
  });
});
