import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { computeTreeHash } from "../../../src/control-plane/contract/invariants.js";

describe("hashDirectory", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-content-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("returns the same hash as computeTreeHash for an empty directory", () => {
    expect(hashDirectory(dir)).toBe(computeTreeHash([]));
  });

  test("hashes a single top-level file deterministically", () => {
    writeFileSync(join(dir, "a.txt"), "hello");
    const h1 = hashDirectory(dir);
    const h2 = hashDirectory(dir);
    expect(h1).toBe(h2);
    expect(h1).toMatch(/^sha256:[0-9a-f]{64}$/);
  });

  test("uses POSIX-style paths regardless of platform", () => {
    mkdirSync(join(dir, "sub"));
    writeFileSync(join(dir, "sub", "b.txt"), "B");
    const expected = computeTreeHash([
      { path: "sub/b.txt", content: new TextEncoder().encode("B") },
    ]);
    expect(hashDirectory(dir)).toBe(expected);
  });

  test("includes nested files with relative paths", () => {
    mkdirSync(join(dir, "x", "y"), { recursive: true });
    writeFileSync(join(dir, "top.txt"), "top");
    writeFileSync(join(dir, "x", "mid.txt"), "mid");
    writeFileSync(join(dir, "x", "y", "leaf.txt"), "leaf");
    const expected = computeTreeHash([
      { path: "top.txt", content: new TextEncoder().encode("top") },
      { path: "x/mid.txt", content: new TextEncoder().encode("mid") },
      { path: "x/y/leaf.txt", content: new TextEncoder().encode("leaf") },
    ]);
    expect(hashDirectory(dir)).toBe(expected);
  });

  test("changing a file changes the hash", () => {
    writeFileSync(join(dir, "a.txt"), "v1");
    const before = hashDirectory(dir);
    writeFileSync(join(dir, "a.txt"), "v2");
    const after = hashDirectory(dir);
    expect(before).not.toBe(after);
  });
});
