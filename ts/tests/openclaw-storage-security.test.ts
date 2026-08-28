import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { AppSettingsSchema } from "../src/config/index.js";
import { ensureSafeArtifactId } from "../src/openclaw/artifact-contract.js";
import { OpenClawService } from "../src/openclaw/service.js";

const roots: string[] = [];
const artifactIdParity = JSON.parse(readFileSync(
  join(import.meta.dirname, "../../fixtures/openclaw-artifact-id-parity.json"),
  "utf-8",
)) as { accepted: string[]; rejected: string[] };

function temporaryRoot(prefix: string): string {
  const root = mkdtempSync(join(tmpdir(), prefix));
  roots.push(root);
  return root;
}

function service(knowledgeRoot: string): OpenClawService {
  return new OpenClawService({
    knowledgeRoot,
    settings: AppSettingsSchema.parse({}),
    openStore: () => {
      throw new Error("not used");
    },
  });
}

function policyArtifact(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "policy-1",
    name: "Policy",
    artifact_type: "policy",
    scenario: "grid_ctf",
    version: 1,
    provenance: {
      run_id: "run-1",
      generation: 1,
      scenario: "grid_ctf",
      settings: {},
    },
    source_code: "def policy(state):\n    return {}\n",
    ...overrides,
  };
}

function harnessArtifact(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return policyArtifact({
    id: "harness-1",
    name: "Harness",
    artifact_type: "harness",
    source_code: "def validate(state, strategy):\n    return True\n",
    ...overrides,
  });
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("OpenClaw private artifact storage", () => {
  it("uses the same storage-safe artifact ID grammar as the Python contract", () => {
    for (const artifactId of artifactIdParity.accepted) {
      expect(ensureSafeArtifactId(artifactId)).toBe(artifactId);
    }
    for (const artifactId of artifactIdParity.rejected) {
      expect(() => ensureSafeArtifactId(artifactId)).toThrow("invalid artifact id");
    }
  });

  it("reads legacy dotted artifact files without allowing new dotted writes", () => {
    const root = temporaryRoot("autoctx-openclaw-legacy-dot-");
    const knowledge = join(root, "knowledge");
    const artifactDir = join(knowledge, "_openclaw_artifacts");
    mkdirSync(artifactDir, { recursive: true });
    const legacy = policyArtifact({ id: "policy.v1" });
    writeFileSync(
      join(artifactDir, "policy.v1.json"),
      JSON.stringify(legacy),
      "utf-8",
    );
    const api = service(knowledge);

    expect(api.fetchArtifact("policy.v1")).toMatchObject({ id: "policy.v1" });
    expect(() => api.publishArtifact(policyArtifact({ id: "policy.v2" })))
      .toThrow("invalid artifact id");
    expect(() => api.fetchArtifact("../policy.v1"))
      .toThrow("invalid legacy artifact id");
  });

  it("rejects a symbolic-link knowledge root", () => {
    const container = temporaryRoot("autoctx-openclaw-root-link-");
    const outside = join(container, "outside");
    mkdirSync(outside);
    const knowledgeLink = join(container, "knowledge");
    symlinkSync(outside, knowledgeLink, "dir");

    expect(() => service(knowledgeLink).publishArtifact(policyArtifact()))
      .toThrow("symbolic-link");
    expect(readdirSync(outside)).toEqual([]);
  });

  it("publishes artifact files with private permissions", () => {
    const root = temporaryRoot("autoctx-openclaw-private-mode-");
    const knowledge = join(root, "knowledge");
    const result = service(knowledge).publishArtifact(policyArtifact());

    if (process.platform !== "win32") {
      expect(statSync(result.path as string).mode & 0o777).toBe(0o600);
    }
  });

  it("rejects symbolic-link artifact directories and final destinations", () => {
    const root = temporaryRoot("autoctx-openclaw-artifact-link-");
    const knowledge = join(root, "knowledge");
    const outside = join(root, "outside");
    mkdirSync(knowledge);
    mkdirSync(outside);
    symlinkSync(outside, join(knowledge, "_openclaw_artifacts"), "dir");

    expect(() => service(knowledge).publishArtifact(policyArtifact()))
      .toThrow("symbolic-link");
    expect(readdirSync(outside)).toEqual([]);

    const secondKnowledge = join(root, "knowledge-2");
    const artifactDir = join(secondKnowledge, "_openclaw_artifacts");
    mkdirSync(artifactDir, { recursive: true });
    const sentinel = join(root, "sentinel.json");
    writeFileSync(sentinel, "sentinel", "utf-8");
    symlinkSync(sentinel, join(artifactDir, "policy-1.json"));
    expect(() => service(secondKnowledge).publishArtifact(policyArtifact()))
      .toThrow("symbolic-link");
    expect(readFileSync(sentinel, "utf-8")).toBe("sentinel");
  });

  it("rejects scenario and harness directory links before publishing harness metadata", () => {
    const root = temporaryRoot("autoctx-openclaw-harness-link-");
    const knowledge = join(root, "knowledge");
    const outside = join(root, "outside");
    mkdirSync(knowledge);
    mkdirSync(outside);
    symlinkSync(outside, join(knowledge, "grid_ctf"), "dir");

    expect(() => service(knowledge).publishArtifact(harnessArtifact()))
      .toThrow("symbolic-link");
    expect(existsSync(join(knowledge, "_openclaw_artifacts", "harness-1.json"))).toBe(false);
    expect(readdirSync(outside)).toEqual([]);

    const secondKnowledge = join(root, "knowledge-2");
    const scenario = join(secondKnowledge, "grid_ctf");
    mkdirSync(scenario, { recursive: true });
    symlinkSync(outside, join(scenario, "harness"), "dir");
    expect(() => service(secondKnowledge).publishArtifact(harnessArtifact()))
      .toThrow("symbolic-link");
    expect(existsSync(join(secondKnowledge, "_openclaw_artifacts", "harness-1.json")))
      .toBe(false);
  });

  it("enforces source and serialized artifact size bounds before writing", () => {
    const root = temporaryRoot("autoctx-openclaw-size-");
    const knowledge = join(root, "knowledge");
    const api = service(knowledge);

    expect(() => api.publishArtifact(policyArtifact({
      source_code: "x".repeat(1024 * 1024 + 1),
    }))).toThrow("source_code exceeds");
    expect(existsSync(join(knowledge, "_openclaw_artifacts"))).toBe(false);

    expect(() => api.publishArtifact({
      id: "model-1",
      name: "Model",
      artifact_type: "distilled_model",
      scenario: "grid_ctf",
      version: 1,
      provenance: {
        run_id: "run-1",
        generation: 1,
        scenario: "grid_ctf",
        settings: {},
      },
      architecture: "tiny",
      parameter_count: 1,
      checkpoint_path: "/models/tiny",
      training_data_stats: { padding: "x".repeat(2 * 1024 * 1024) },
    })).toThrow("artifact JSON exceeds");
    expect(existsSync(join(knowledge, "_openclaw_artifacts"))).toBe(false);
  });

  it("rejects overlong artifact and scenario identifiers before filesystem access", () => {
    const root = temporaryRoot("autoctx-openclaw-id-size-");
    const knowledge = join(root, "knowledge");
    const api = service(knowledge);

    expect(() => api.publishArtifact(policyArtifact({ id: `a${"x".repeat(128)}` })))
      .toThrow("invalid artifact id");
    expect(() => api.publishArtifact(policyArtifact({ scenario: `s${"x".repeat(128)}` })))
      .toThrow("scenario exceeds");
    expect(existsSync(knowledge)).toBe(false);
  });
});
