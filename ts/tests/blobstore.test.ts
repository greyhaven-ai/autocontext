/**
 * AC-518 Phase 3: TypeScript blob store parity tests.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { LocalBlobStore } from "../src/blobstore/local.js";
import { BlobRegistry } from "../src/blobstore/registry.js";
import { HydrationCache } from "../src/blobstore/cache.js";
import { BlobMirror } from "../src/blobstore/mirror.js";
import { SyncManager } from "../src/blobstore/sync.js";
import { createBlobRef, isHydrated } from "../src/blobstore/ref.js";
import { createBlobStore } from "../src/blobstore/factory.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac518-ts-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("LocalBlobStore", () => {
  it("put and get roundtrip", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const data = Buffer.from('{"event":"start"}\n');
    store.put("runs/r1/events.ndjson", data);
    expect(store.get("runs/r1/events.ndjson")).toEqual(data);
  });

  it("put returns sha256 digest", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const data = Buffer.from("hello");
    const digest = store.put("test.txt", data);
    const expected =
      "sha256:" + createHash("sha256").update(data).digest("hex");
    expect(digest).toBe(expected);
  });

  it("get returns null for missing key", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    expect(store.get("missing")).toBeNull();
  });

  it("head returns metadata", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    store.put("test.txt", Buffer.from("content"));
    const meta = store.head("test.txt");
    expect(meta).not.toBeNull();
    expect(meta!.sizeBytes).toBe(7);
    expect(meta!.digest).toMatch(/^sha256:/);
  });

  it("listPrefix filters correctly", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    store.put("runs/r1/a.txt", Buffer.from("a"));
    store.put("runs/r1/b.txt", Buffer.from("b"));
    store.put("runs/r2/c.txt", Buffer.from("c"));
    const keys = store.listPrefix("runs/r1/");
    expect(keys.sort()).toEqual(["runs/r1/a.txt", "runs/r1/b.txt"]);
  });

  it("delete removes key", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    store.put("del.txt", Buffer.from("x"));
    expect(store.delete("del.txt")).toBe(true);
    expect(store.get("del.txt")).toBeNull();
  });

  it("putFile and getFile work", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const src = join(tmpDir, "source.bin");
    writeFileSync(src, "binary data");
    store.putFile("test.bin", src);
    const dest = join(tmpDir, "dest.bin");
    expect(store.getFile("test.bin", dest)).toBe(true);
    expect(readFileSync(dest, "utf-8")).toBe("binary data");
  });

  it("rejects escaping blob keys", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    expect(() => store.put("../escape.txt", Buffer.from("x"))).toThrow(
      "invalid blob key",
    );
  });

  it("wraps unreadable blob entries with key context", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    mkdirSync(join(tmpDir, "blobs", "dir-key"), { recursive: true });

    expect(() => store.get("dir-key")).toThrow("Failed to read blob 'dir-key'");
    expect(() => store.head("dir-key")).toThrow("Failed to read blob metadata 'dir-key'");
  });
});

describe("BlobRegistry", () => {
  it("register and lookup", () => {
    const registry = new BlobRegistry();
    const ref = createBlobRef({
      kind: "trace",
      digest: "sha256:abc",
      sizeBytes: 100,
    });
    registry.register("r1", "events.ndjson", ref);
    expect(registry.lookup("r1", "events.ndjson")).toBe(ref);
  });

  it("save and load roundtrip", () => {
    const registry = new BlobRegistry();
    registry.register(
      "r1",
      "f.txt",
      createBlobRef({ kind: "trace", digest: "sha256:x", sizeBytes: 50 }),
    );
    const path = join(tmpDir, "registry.json");
    registry.save(path);
    const loaded = BlobRegistry.load(path);
    expect(loaded.lookup("r1", "f.txt")).not.toBeNull();
  });

  it("ignores malformed registry payloads instead of registering partial refs", () => {
    const path = join(tmpDir, "registry.json");
    writeFileSync(path, JSON.stringify({ r1: { "bad.txt": { kind: "trace" } } }), "utf-8");

    const loaded = BlobRegistry.load(path);

    expect(loaded.lookup("r1", "bad.txt")).toBeNull();
  });
});

describe("HydrationCache", () => {
  it("put and get with digest verification", () => {
    const cache = new HydrationCache(join(tmpDir, "cache"), 100);
    const data = Buffer.from("cached");
    const digest = "sha256:" + createHash("sha256").update(data).digest("hex");
    cache.put("test.txt", data, digest);
    expect(cache.get("test.txt", digest)).toEqual(data);
  });

  it("rejects corrupted cache entries", () => {
    const cache = new HydrationCache(join(tmpDir, "cache"), 100);
    const data = Buffer.from("original");
    const digest = "sha256:" + createHash("sha256").update(data).digest("hex");
    cache.put("test.txt", data, digest);
    writeFileSync(join(tmpDir, "cache", "test.txt"), "corrupted");
    expect(cache.get("test.txt", digest)).toBeNull();
  });

  it("rejects escaping cache keys", () => {
    const cache = new HydrationCache(join(tmpDir, "cache"), 100);
    const data = Buffer.from("cached");
    const digest = "sha256:" + createHash("sha256").update(data).digest("hex");
    expect(() => cache.put("../escape.txt", data, digest)).toThrow(
      "invalid blob key",
    );
  });

  it("wraps unreadable cache entries with key context", () => {
    const cache = new HydrationCache(join(tmpDir, "cache"), 100);
    mkdirSync(join(tmpDir, "cache", "dir-key"), { recursive: true });

    expect(() => cache.get("dir-key")).toThrow("Failed to read cache entry 'dir-key'");
  });
});

describe("BlobMirror", () => {
  it("mirrors artifact to store", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const mirror = new BlobMirror(store, 0);
    const ref = mirror.mirrorArtifact("test.txt", Buffer.from("data"), "trace");
    expect(ref).not.toBeNull();
    expect(ref!.kind).toBe("trace");
    expect(store.get("test.txt")).toEqual(Buffer.from("data"));
  });

  it("skips small artifacts", () => {
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const mirror = new BlobMirror(store, 1000);
    expect(
      mirror.mirrorArtifact("tiny.txt", Buffer.from("x"), "trace"),
    ).toBeNull();
  });
});

describe("SyncManager", () => {
  it("syncs a run directory", () => {
    const runDir = join(tmpDir, "runs", "r1");
    mkdirSync(runDir, { recursive: true });
    writeFileSync(join(runDir, "events.ndjson"), '{"e":"start"}');
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const mgr = new SyncManager(store, join(tmpDir, "runs"));
    const result = mgr.syncRun("r1");
    expect(result.syncedCount).toBeGreaterThanOrEqual(1);
    expect(mgr.status().runCount).toBe(1);
  });

  it("re-uploads files that changed since the last sync", () => {
    const runDir = join(tmpDir, "runs", "r1");
    mkdirSync(runDir, { recursive: true });
    writeFileSync(join(runDir, "events.ndjson"), "v1");
    const store = new LocalBlobStore(join(tmpDir, "blobs"));
    const mgr = new SyncManager(store, join(tmpDir, "runs"));

    expect(mgr.syncRun("r1").syncedCount).toBe(1);

    writeFileSync(join(runDir, "events.ndjson"), "v2");
    const second = mgr.syncRun("r1");
    expect(second.syncedCount).toBe(1);
    expect(second.skippedCount).toBe(0);
    expect(store.get("runs/r1/events.ndjson")?.toString("utf-8")).toBe("v2");
  });
});

describe("Factory", () => {
  it("creates local backend", () => {
    const store = createBlobStore({
      backend: "local",
      root: join(tmpDir, "blobs"),
    });
    expect(store).toBeDefined();
    expect(typeof store.put).toBe("function");
  });

  it("throws for unknown backend", () => {
    expect(() => createBlobStore({ backend: "s3" })).toThrow("Unknown");
  });
});
