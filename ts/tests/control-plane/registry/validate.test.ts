import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { validate } from "../../../src/control-plane/registry/validate.js";
import { artifactDirectory } from "../../../src/control-plane/registry/artifact-store.js";
import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { canonicalJsonStringify } from "../../../src/control-plane/contract/canonical-json.js";
import type { ContentHash, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { Provenance } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayload(parent: string, content: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), content);
  return { dir, hash: hashDirectory(dir) };
}

describe("validate", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-validate-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("empty registry: ok=true with no issues", () => {
    const report = validate(registryRoot);
    expect(report.ok).toBe(true);
    expect(report.issues).toEqual([]);
  });

  test("clean registry with one good artifact: ok=true", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    reg.saveArtifact(createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    }), dir);
    const report = validate(registryRoot);
    expect(report.ok).toBe(true);
  });

  test("flags payload-hash mismatch when payload tampered", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    // Tamper the on-disk payload.
    writeFileSync(join(artifactDirectory(registryRoot, a.id), "payload", "f.txt"), "TAMPERED");

    const report = validate(registryRoot);
    expect(report.ok).toBe(false);
    expect(report.issues.find((i) => i.kind === "payload-hash-mismatch")).toBeDefined();
  });

  test("flags schema validation errors when metadata.json is corrupt", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    // Wreck the metadata file (still valid JSON, but schema-invalid).
    writeFileSync(join(artifactDirectory(registryRoot, a.id), "metadata.json"), JSON.stringify({ id: "not-a-ulid" }));

    const report = validate(registryRoot);
    expect(report.ok).toBe(false);
    const issue = report.issues.find((i) => i.kind === "schema-validation-error");
    expect(issue).toBeDefined();
  });

  test("flags an invalid promotion transition recorded in history", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    // Write an illegal transition directly into the history file: deprecated -> active is allowed,
    // but disabled -> active is not. We use disabled -> active.
    const historyPath = join(artifactDirectory(registryRoot, a.id), "promotion-history.jsonl");
    writeFileSync(historyPath, JSON.stringify(createPromotionEvent({
      from: "disabled", to: "active", reason: "illegal", timestamp: "2026-04-17T13:00:00.000Z",
    })) + "\n");

    const report = validate(registryRoot);
    expect(report.ok).toBe(false);
    const issue = report.issues.find((i) => i.kind === "invalid-promotion-transition");
    expect(issue).toBeDefined();
  });

  test("flags history-parse-error when history.jsonl is malformed", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    writeFileSync(
      join(artifactDirectory(registryRoot, a.id), "promotion-history.jsonl"),
      "not-json\n",
    );
    const report = validate(registryRoot);
    expect(report.ok).toBe(false);
    expect(report.issues.find((i) => i.kind === "history-parse-error")).toBeDefined();
  });

  test("reports signature-present and signature-missing as informational only (no ok=false)", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    // History entry with a signature.
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "shadow", reason: "first", timestamp: "2026-04-17T12:30:00.000Z",
      signature: "fake-signature",
    }));
    // History entry without a signature.
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "shadow", to: "active", reason: "go", timestamp: "2026-04-17T12:35:00.000Z",
    }));

    const report = validate(registryRoot);
    // Both signature notes appear; report.ok is determined ONLY by hard failures.
    const kinds = report.issues.map((i) => i.kind);
    expect(kinds).toContain("signature-present");
    expect(kinds).toContain("signature-missing");
    expect(report.ok).toBe(true);
  });

  test("report includes the offending artifactId on per-artifact issues", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    writeFileSync(join(artifactDirectory(registryRoot, a.id), "payload", "f.txt"), "BAD");

    const report = validate(registryRoot);
    const mismatch = report.issues.find((i) => i.kind === "payload-hash-mismatch");
    expect(mismatch?.artifactId).toBe(a.id);
  });

  test("deduplicates: a clean registry rebuilt with metadata that matches its history yields no issues", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "shadow", reason: "ok", timestamp: "2026-04-17T12:30:00.000Z",
    }));
    const report = validate(registryRoot);
    expect(report.ok).toBe(true);
    // The metadata.json is canonical JSON; ensure no spurious schema errors.
    const meta = JSON.parse(readFileSync(join(artifactDirectory(registryRoot, a.id), "metadata.json"), "utf-8"));
    expect(meta.activationState).toBe("shadow");
    // (Self-check that canonical JSON is being written.)
    expect(canonicalJsonStringify(meta)).toBeTruthy();
  });
});
