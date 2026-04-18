import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createFsIndexCache } from "../../../src/control-plane/registry/index-cache.js";
import { saveArtifact } from "../../../src/control-plane/registry/artifact-store.js";
import { writeStatePointer } from "../../../src/control-plane/registry/state-pointer.js";
import { createArtifact } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import type { ContentHash, EnvironmentTag, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, Provenance } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayload(parent: string, name: string, content: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), content);
  return { dir, hash: hashDirectory(dir) };
}

function makeAndSave(registryRoot: string, opts: { scenario: string; tag?: string; payload?: string; state?: Artifact["activationState"] } = { scenario: "grid_ctf" }): Artifact {
  const { dir, hash } = tempPayload(registryRoot, opts.scenario + "-" + Math.random().toString(36).slice(2), opts.payload ?? "x");
  const artifact = createArtifact({
    actuatorType: "prompt-patch",
    scenario: opts.scenario as Scenario,
    environmentTag: (opts.tag ?? "production") as EnvironmentTag,
    payloadHash: hash,
    provenance: aProvenance,
  });
  // For "active" state we override after creation (factories build candidate-only).
  const final: Artifact = opts.state ? { ...artifact, activationState: opts.state } : artifact;
  saveArtifact(registryRoot, final, dir);
  return final;
}

describe("createFsIndexCache", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-index-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("listCandidates with no filter returns every saved artifact", () => {
    const a = makeAndSave(registryRoot, { scenario: "grid_ctf" });
    const b = makeAndSave(registryRoot, { scenario: "othello" });
    const cache = createFsIndexCache(registryRoot);
    const result = cache.listCandidates({});
    const ids = result.map((r) => r.id).sort();
    expect(ids).toEqual([a.id, b.id].sort());
  });

  test("listCandidates filters by scenario", () => {
    const a = makeAndSave(registryRoot, { scenario: "grid_ctf" });
    makeAndSave(registryRoot, { scenario: "othello" });
    const cache = createFsIndexCache(registryRoot);
    const result = cache.listCandidates({ scenario: "grid_ctf" as Scenario });
    expect(result.map((r) => r.id)).toEqual([a.id]);
  });

  test("listCandidates filters by environmentTag", () => {
    const a = makeAndSave(registryRoot, { scenario: "grid_ctf", tag: "staging" });
    makeAndSave(registryRoot, { scenario: "grid_ctf", tag: "production" });
    const cache = createFsIndexCache(registryRoot);
    const result = cache.listCandidates({ environmentTag: "staging" as EnvironmentTag });
    expect(result.map((r) => r.id)).toEqual([a.id]);
  });

  test("listCandidates filters by activationState", () => {
    const active = makeAndSave(registryRoot, { scenario: "grid_ctf", state: "active" });
    makeAndSave(registryRoot, { scenario: "grid_ctf" }); // candidate
    const cache = createFsIndexCache(registryRoot);
    const result = cache.listCandidates({ activationState: "active" });
    expect(result.map((r) => r.id)).toEqual([active.id]);
  });

  test("getByState returns the artifact pointed to by state/active/<...>", () => {
    const active = makeAndSave(registryRoot, { scenario: "grid_ctf", state: "active" });
    writeStatePointer(registryRoot, "grid_ctf" as Scenario, "prompt-patch", "production" as EnvironmentTag, {
      artifactId: active.id,
      asOf: "2026-04-17T12:00:00.000Z",
    });
    const cache = createFsIndexCache(registryRoot);
    const found = cache.getByState("grid_ctf" as Scenario, "prompt-patch", "production" as EnvironmentTag);
    expect(found?.id).toBe(active.id);
  });

  test("getByState returns null when no pointer exists", () => {
    const cache = createFsIndexCache(registryRoot);
    expect(cache.getByState("grid_ctf" as Scenario, "prompt-patch", "production" as EnvironmentTag)).toBeNull();
  });

  test("listCandidates returns [] when no candidates dir exists", () => {
    const cache = createFsIndexCache(registryRoot);
    expect(cache.listCandidates({})).toEqual([]);
  });
});
