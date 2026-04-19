import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { acquireLock, type LockHandle } from "../../../../src/production-traces/ingest/lock.js";

describe("production-traces ingest lock", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-ingest-lock-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("creates the lock file at .autocontext/lock under the cwd", () => {
    const handle = acquireLock(dir);
    try {
      expect(existsSync(join(dir, ".autocontext", "lock"))).toBe(true);
    } finally {
      handle.release();
    }
  });

  test("a second overlapping acquire on the same dir throws", () => {
    const a = acquireLock(dir);
    try {
      expect(() => acquireLock(dir)).toThrow();
    } finally {
      a.release();
    }
  });

  test("releases cleanly so a subsequent acquire succeeds", () => {
    const a = acquireLock(dir);
    a.release();
    const b = acquireLock(dir);
    b.release();
  });

  test("release is idempotent (calling twice does not throw)", () => {
    const a: LockHandle = acquireLock(dir);
    a.release();
    expect(() => a.release()).not.toThrow();
  });

  test("shares the lock file with Foundation B registry (same path)", async () => {
    // Both this lock and control-plane/registry/lock.ts target <root>/.autocontext/lock.
    // OS-level flock enforces single-writer across both.
    const { acquireLock: acquireRegistryLock } = await import(
      "../../../../src/control-plane/registry/lock.js"
    );
    const a = acquireLock(dir);
    try {
      expect(() => acquireRegistryLock(dir)).toThrow();
    } finally {
      a.release();
    }
    // And the converse: registry holds, ingest is blocked.
    const b = acquireRegistryLock(dir);
    try {
      expect(() => acquireLock(dir)).toThrow();
    } finally {
      b.release();
    }
  });

  test("survives synchronous filesystem operations between acquire and release", () => {
    const a = acquireLock(dir);
    try {
      writeFileSync(join(dir, "scratch.txt"), "x");
      expect(() => acquireLock(dir)).toThrow();
    } finally {
      a.release();
    }
  });
});
