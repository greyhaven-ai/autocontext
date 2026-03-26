/**
 * Tests for AC-416: Proof mission spike — formal verifier contracts.
 *
 * - ProofStatus enum (draft, informal, checking, verified, rejected)
 * - ProofMissionSpec schema
 * - LeanVerifier: runs `lake build` for Lean 4 proofs
 * - ProofEvidence: tracks proof state distinct from model self-report
 * - createProofMission factory
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-proof-"));
}

// ---------------------------------------------------------------------------
// ProofStatus — distinguishes draft from formally verified
// ---------------------------------------------------------------------------

describe("ProofStatus", () => {
  it("ProofStatusSchema has correct values", async () => {
    const { ProofStatusSchema } = await import("../src/mission/proof.js");
    expect(ProofStatusSchema.parse("draft")).toBe("draft");
    expect(ProofStatusSchema.parse("informal")).toBe("informal");
    expect(ProofStatusSchema.parse("checking")).toBe("checking");
    expect(ProofStatusSchema.parse("verified")).toBe("verified");
    expect(ProofStatusSchema.parse("rejected")).toBe("rejected");
  });

  it("isHardVerified returns true only for verified status", async () => {
    const { isHardVerified } = await import("../src/mission/proof.js");
    expect(isHardVerified("verified")).toBe(true);
    expect(isHardVerified("draft")).toBe(false);
    expect(isHardVerified("informal")).toBe(false);
  });

  it("isAdvisory returns true for draft and informal", async () => {
    const { isAdvisory } = await import("../src/mission/proof.js");
    expect(isAdvisory("draft")).toBe(true);
    expect(isAdvisory("informal")).toBe(true);
    expect(isAdvisory("verified")).toBe(false);
    expect(isAdvisory("rejected")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ProofMissionSpec
// ---------------------------------------------------------------------------

describe("ProofMissionSpec", () => {
  it("validates a Lean proof mission spec", async () => {
    const { ProofMissionSpecSchema } = await import("../src/mission/proof.js");
    const spec = ProofMissionSpecSchema.parse({
      name: "Prove Fermat's Last Theorem for n=3",
      goal: "Formally verify the proof in Lean 4",
      proofAssistant: "lean4",
      projectPath: "/path/to/lean-project",
      buildCommand: "lake build",
      theoremName: "FermatLastN3",
    });
    expect(spec.proofAssistant).toBe("lean4");
    expect(spec.theoremName).toBe("FermatLastN3");
  });

  it("supports coq and isabelle proof assistants", async () => {
    const { ProofMissionSpecSchema } = await import("../src/mission/proof.js");
    expect(ProofMissionSpecSchema.parse({
      name: "t", goal: "g", proofAssistant: "coq",
      projectPath: ".", buildCommand: "coqc proof.v",
    }).proofAssistant).toBe("coq");
    expect(ProofMissionSpecSchema.parse({
      name: "t", goal: "g", proofAssistant: "isabelle",
      projectPath: ".", buildCommand: "isabelle build",
    }).proofAssistant).toBe("isabelle");
  });
});

// ---------------------------------------------------------------------------
// LeanVerifier
// ---------------------------------------------------------------------------

describe("LeanVerifier", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("has label and proofAssistant properties", async () => {
    const { LeanVerifier } = await import("../src/mission/proof.js");
    const verifier = new LeanVerifier("lake build", dir);
    expect(verifier.label).toContain("lean");
    expect(verifier.proofAssistant).toBe("lean4");
  });

  it("passes when build command succeeds (exit 0)", async () => {
    const { LeanVerifier } = await import("../src/mission/proof.js");
    const verifier = new LeanVerifier("true", dir);
    const result = await verifier.verify("m-1");
    expect(result.passed).toBe(true);
    expect(result.metadata?.proofStatus).toBe("verified");
  });

  it("fails when build command fails", async () => {
    const { LeanVerifier } = await import("../src/mission/proof.js");
    const verifier = new LeanVerifier("false", dir);
    const result = await verifier.verify("m-1");
    expect(result.passed).toBe(false);
    expect(result.metadata?.proofStatus).toBe("rejected");
  });

  it("includes advisory label when proof is not formally verified", async () => {
    const { LeanVerifier } = await import("../src/mission/proof.js");
    const verifier = new LeanVerifier("false", dir);
    const result = await verifier.verify("m-1");
    expect(result.reason).toContain("not formally verified");
  });
});

// ---------------------------------------------------------------------------
// createProofMission factory
// ---------------------------------------------------------------------------

describe("createProofMission", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("creates mission with proof verifier wired", async () => {
    const { createProofMission } = await import("../src/mission/proof.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createProofMission(manager, {
      name: "Prove theorem",
      goal: "Formally verify",
      proofAssistant: "lean4",
      projectPath: dir,
      buildCommand: "true",
    });

    expect(manager.get(id)!.status).toBe("active");
    expect(manager.get(id)!.metadata).toEqual(expect.objectContaining({
      missionType: "proof",
      proofAssistant: "lean4",
    }));

    const result = await manager.verify(id);
    expect(result.passed).toBe(true);
    expect(result.metadata?.proofStatus).toBe("verified");
    manager.close();
  });

  it("stores proofAssistant and theoremName in metadata", async () => {
    const { createProofMission } = await import("../src/mission/proof.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = createProofMission(manager, {
      name: "Prove",
      goal: "Verify",
      proofAssistant: "lean4",
      projectPath: dir,
      buildCommand: "true",
      theoremName: "MyTheorem",
    });

    const meta = manager.get(id)!.metadata as Record<string, unknown>;
    expect(meta.theoremName).toBe("MyTheorem");
    expect(meta.proofAssistant).toBe("lean4");
    manager.close();
  });
});

// ---------------------------------------------------------------------------
// SUPPORTED_PROOF_ASSISTANTS
// ---------------------------------------------------------------------------

describe("Supported proof assistants", () => {
  it("exports list of supported proof assistants with metadata", async () => {
    const { SUPPORTED_PROOF_ASSISTANTS } = await import("../src/mission/proof.js");
    expect(SUPPORTED_PROOF_ASSISTANTS.length).toBeGreaterThanOrEqual(3);
    const ids = SUPPORTED_PROOF_ASSISTANTS.map((p) => p.id);
    expect(ids).toContain("lean4");
    expect(ids).toContain("coq");
    expect(ids).toContain("isabelle");
  });

  it("each entry has id, name, and defaultBuildCommand", async () => {
    const { SUPPORTED_PROOF_ASSISTANTS } = await import("../src/mission/proof.js");
    for (const pa of SUPPORTED_PROOF_ASSISTANTS) {
      expect(typeof pa.id).toBe("string");
      expect(typeof pa.name).toBe("string");
      expect(typeof pa.defaultBuildCommand).toBe("string");
    }
  });
});
