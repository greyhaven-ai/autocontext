import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { acquireLock, type LockHandle } from "../../../src/control-plane/registry/lock.js";

describe("acquireLock", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-lock-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("creates the lock file at .autocontext/lock under the registry root", () => {
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

  test("survives synchronous filesystem operations between acquire and release", () => {
    const a = acquireLock(dir);
    try {
      // Doing other sync work shouldn't release the lock.
      writeFileSync(join(dir, "scratch.txt"), "x");
      expect(() => acquireLock(dir)).toThrow();
    } finally {
      a.release();
    }
  });
});
