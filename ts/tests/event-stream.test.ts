/**
 * Tests for AC-342 Task 3: Event Stream Emitter — NDJSON file + subscriber dispatch.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-events-"));
}

describe("EventStreamEmitter", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("should be importable", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    expect(EventStreamEmitter).toBeDefined();
  });

  it("should create parent directories and write NDJSON", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "sub", "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    emitter.emit("generation_started", { runId: "run-1", generation: 1 });

    expect(existsSync(path)).toBe(true);
    const content = readFileSync(path, "utf-8").trim();
    const parsed = JSON.parse(content);
    expect(parsed.event).toBe("generation_started");
    expect(parsed.payload.runId).toBe("run-1");
    expect(parsed.v).toBe(1);
    expect(parsed.seq).toBe(1);
  });

  it("should increment sequence numbers", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    emitter.emit("event_a", { a: 1 });
    emitter.emit("event_b", { b: 2 });
    emitter.emit("event_c", { c: 3 });

    const lines = readFileSync(path, "utf-8").trim().split("\n");
    expect(lines).toHaveLength(3);
    expect(JSON.parse(lines[0]).seq).toBe(1);
    expect(JSON.parse(lines[1]).seq).toBe(2);
    expect(JSON.parse(lines[2]).seq).toBe(3);
  });

  it("should include ISO timestamp", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    emitter.emit("test", {});

    const line = JSON.parse(readFileSync(path, "utf-8").trim());
    expect(line.ts).toBeDefined();
    // Should be a valid ISO string
    const date = new Date(line.ts);
    expect(date.getTime()).not.toBeNaN();
  });

  it("should support channel parameter", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    emitter.emit("test", {}, "ecosystem");

    const line = JSON.parse(readFileSync(path, "utf-8").trim());
    expect(line.channel).toBe("ecosystem");
  });

  it("should default channel to 'generation'", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    emitter.emit("test", {});

    const line = JSON.parse(readFileSync(path, "utf-8").trim());
    expect(line.channel).toBe("generation");
  });

  it("should dispatch to subscribers", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];
    emitter.subscribe((event: string, payload: Record<string, unknown>) => {
      received.push({ event, payload });
    });

    emitter.emit("gen_started", { gen: 1 });
    emitter.emit("gen_completed", { gen: 1, score: 0.8 });

    expect(received).toHaveLength(2);
    expect(received[0].event).toBe("gen_started");
    expect(received[1].payload.score).toBe(0.8);
  });

  it("should support multiple subscribers", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    let count1 = 0;
    let count2 = 0;
    emitter.subscribe(() => { count1++; });
    emitter.subscribe(() => { count2++; });

    emitter.emit("test", {});
    expect(count1).toBe(1);
    expect(count2).toBe(1);
  });

  it("should unsubscribe correctly", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    let count = 0;
    const cb = () => { count++; };
    emitter.subscribe(cb);
    emitter.emit("test", {});
    expect(count).toBe(1);

    emitter.unsubscribe(cb);
    emitter.emit("test", {});
    expect(count).toBe(1); // should not increment
  });

  it("should not crash when subscriber throws", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const path = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(path);

    let secondCalled = false;
    emitter.subscribe(() => { throw new Error("boom"); });
    emitter.subscribe(() => { secondCalled = true; });

    // Should not throw
    emitter.emit("test", { x: 1 });
    expect(secondCalled).toBe(true);

    // File should still be written
    const content = readFileSync(path, "utf-8").trim();
    expect(content.length).toBeGreaterThan(0);
  });
});
