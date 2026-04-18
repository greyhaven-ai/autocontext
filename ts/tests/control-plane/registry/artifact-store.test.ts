import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  saveArtifact,
  loadArtifact,
  listArtifactIds,
} from "../../../src/control-plane/registry/artifact-store.js";
import { createArtifact } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import type {
  Artifact,
  Provenance,
} from "../../../src/control-plane/contract/types.js";
import type { ContentHash } from "../../../src/control-plane/contract/branded-ids.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayloadDir(parent: string, files: Record<string, string>): {
  dir: string;
  hash: ContentHash;
} {
  const payload = join(parent, "payload-src");
  mkdirSync(payload, { recursive: true });
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(payload, rel);
    mkdirSync(join(abs, ".."), { recursive: true });
    writeFileSync(abs, content);
  }
  return { dir: payload, hash: hashDirectory(payload) };
}

describe("saveArtifact / loadArtifact round-trip", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-artifact-store-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("saves an artifact with payload files and reads it back identically", () => {
    const { dir: payloadDir, hash } = tempPayloadDir(registryRoot, {
      "prompt.md": "# system\nhello",
      "config.json": '{"k":1}',
    });
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });

    saveArtifact(registryRoot, artifact, payloadDir);

    const candidatesDir = join(registryRoot, ".autocontext", "candidates", artifact.id);
    expect(existsSync(join(candidatesDir, "metadata.json"))).toBe(true);
    expect(existsSync(join(candidatesDir, "payload"))).toBe(true);
    expect(existsSync(join(candidatesDir, "payload.sha256"))).toBe(true);

    const loaded = loadArtifact(registryRoot, artifact.id);
    expect(loaded).toEqual<Artifact>(artifact);
  });

  test("payload.sha256 sidecar contains the canonical hash string", () => {
    const { dir: payloadDir, hash } = tempPayloadDir(registryRoot, {
      "x.txt": "x",
    });
    const artifact = createArtifact({
      actuatorType: "tool-policy",
      scenario: "othello",
      payloadHash: hash,
      provenance: aProvenance,
    });
    saveArtifact(registryRoot, artifact, payloadDir);
    const sidecar = readFileSync(
      join(registryRoot, ".autocontext", "candidates", artifact.id, "payload.sha256"),
      "utf-8",
    ).trim();
    expect(sidecar).toBe(hash);
  });

  test("loadArtifact refuses if the payload tree hash no longer matches", () => {
    const { dir: payloadDir, hash } = tempPayloadDir(registryRoot, {
      "a.txt": "good",
    });
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    saveArtifact(registryRoot, artifact, payloadDir);

    // Tamper the on-disk payload after save.
    const stored = join(registryRoot, ".autocontext", "candidates", artifact.id, "payload", "a.txt");
    writeFileSync(stored, "bad");

    expect(() => loadArtifact(registryRoot, artifact.id)).toThrow(/payload.*hash.*mismatch/i);
  });

  test("loadArtifact throws when the artifact id is unknown", () => {
    expect(() => loadArtifact(registryRoot, "01KPEYB3BRQWK2WSHK9E93N6NP" as any)).toThrow(/not found/i);
  });

  test("listArtifactIds enumerates every directory under candidates/", () => {
    const ids: string[] = [];
    for (let i = 0; i < 3; i++) {
      const { dir, hash } = tempPayloadDir(join(registryRoot, `tmp-${i}`), {
        "f.txt": String(i),
      });
      const a = createArtifact({
        actuatorType: "prompt-patch",
        scenario: "grid_ctf",
        payloadHash: hash,
        provenance: aProvenance,
      });
      saveArtifact(registryRoot, a, dir);
      ids.push(a.id);
    }
    const seen = listArtifactIds(registryRoot);
    expect(seen.sort()).toEqual([...ids].sort());
  });

  test("listArtifactIds returns empty when no candidates dir exists", () => {
    expect(listArtifactIds(registryRoot)).toEqual([]);
  });
});
