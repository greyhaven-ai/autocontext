/**
 * Proof mission spike — formal verifier contracts (AC-416).
 *
 * Defines the contracts and verifier stubs for theorem/proof missions.
 * Key design principle: only formal verification counts as "verified".
 * Natural language proofs and model self-reports are "advisory" only.
 *
 * Supported proof assistants: Lean 4, Coq, Isabelle
 */

import { execFileSync } from "node:child_process";
import { z } from "zod";
import type { MissionManager } from "./manager.js";
import type { Verifier } from "./verifiers.js";
import type { VerifierResult } from "./types.js";
import { MissionBudgetSchema } from "./types.js";

// ---------------------------------------------------------------------------
// ProofStatus — explicit labeling of proof state
// ---------------------------------------------------------------------------

export const ProofStatusSchema = z.enum([
  "draft",      // Natural language sketch, no formal content
  "informal",   // Structured proof but not machine-checked
  "checking",   // Submitted to proof assistant, awaiting result
  "verified",   // Proof assistant accepted — hard verification
  "rejected",   // Proof assistant found errors
]);

export type ProofStatus = z.infer<typeof ProofStatusSchema>;

/**
 * True only when the proof has been accepted by a formal proof assistant.
 * This is the ONLY state that counts as mission success for proof missions.
 */
export function isHardVerified(status: ProofStatus): boolean {
  return status === "verified";
}

/**
 * True when the proof is in draft or informal state.
 * These results should be labeled as advisory — they do NOT constitute
 * formal verification and should never be reported as proven.
 */
export function isAdvisory(status: ProofStatus): boolean {
  return status === "draft" || status === "informal";
}

// ---------------------------------------------------------------------------
// Supported proof assistants
// ---------------------------------------------------------------------------

export interface ProofAssistantInfo {
  id: string;
  name: string;
  defaultBuildCommand: string;
  fileExtension: string;
}

export const SUPPORTED_PROOF_ASSISTANTS: ProofAssistantInfo[] = [
  { id: "lean4", name: "Lean 4", defaultBuildCommand: "lake build", fileExtension: ".lean" },
  { id: "coq", name: "Coq", defaultBuildCommand: "coqc", fileExtension: ".v" },
  { id: "isabelle", name: "Isabelle", defaultBuildCommand: "isabelle build -d .", fileExtension: ".thy" },
];

// ---------------------------------------------------------------------------
// ProofMissionSpec
// ---------------------------------------------------------------------------

export const ProofMissionSpecSchema = z.object({
  name: z.string(),
  goal: z.string(),
  proofAssistant: z.string(),
  projectPath: z.string(),
  buildCommand: z.string(),
  theoremName: z.string().optional(),
  budget: MissionBudgetSchema.optional(),
  metadata: z.record(z.unknown()).default({}),
});

export type ProofMissionSpec = z.infer<typeof ProofMissionSpecSchema>;

// ---------------------------------------------------------------------------
// LeanVerifier — runs build command for Lean 4 proofs
// ---------------------------------------------------------------------------

export class LeanVerifier implements Verifier {
  readonly label: string;
  readonly proofAssistant = "lean4" as const;
  private readonly buildCommand: string;
  private readonly cwd: string;

  constructor(buildCommand: string, cwd: string) {
    this.buildCommand = buildCommand;
    this.label = `lean4: ${buildCommand}`;
    this.cwd = cwd;
  }

  async verify(_missionId: string): Promise<VerifierResult> {
    try {
      const stdout = execFileSync("/bin/sh", ["-c", this.buildCommand], {
        cwd: this.cwd,
        encoding: "utf-8",
        timeout: 300_000, // 5 minutes for proof checking
        stdio: ["pipe", "pipe", "pipe"],
      });
      return {
        passed: true,
        reason: "Proof formally verified by Lean 4",
        suggestions: [],
        metadata: {
          proofStatus: "verified" as ProofStatus,
          proofAssistant: "lean4",
          stdout: stdout.trim(),
          command: this.buildCommand,
        },
      };
    } catch (err) {
      const exitCode = (err as { status?: number }).status ?? 1;
      const stderr = (err as { stderr?: string }).stderr ?? "";
      return {
        passed: false,
        reason: `Proof not formally verified — build failed (exit ${exitCode})`,
        suggestions: stderr
          ? [`Build errors:\n${stderr.trim().slice(0, 1000)}`]
          : ["Check proof for type errors or unsolved goals"],
        metadata: {
          proofStatus: "rejected" as ProofStatus,
          proofAssistant: "lean4",
          exitCode,
          stderr: stderr.trim().slice(0, 2000),
          command: this.buildCommand,
        },
      };
    }
  }
}

// ---------------------------------------------------------------------------
// createProofMission factory
// ---------------------------------------------------------------------------

export function createProofMission(
  manager: MissionManager,
  spec: ProofMissionSpec,
): string {
  const parsed = ProofMissionSpecSchema.parse(spec);

  const id = manager.create({
    name: parsed.name,
    goal: parsed.goal,
    budget: parsed.budget,
    metadata: {
      ...parsed.metadata,
      missionType: "proof",
      proofAssistant: parsed.proofAssistant,
      projectPath: parsed.projectPath,
      buildCommand: parsed.buildCommand,
      ...(parsed.theoremName ? { theoremName: parsed.theoremName } : {}),
    },
  });

  // Wire appropriate verifier based on proof assistant
  const verifier = new LeanVerifier(parsed.buildCommand, parsed.projectPath);
  manager.setVerifier(id, async (missionId) => verifier.verify(missionId));

  return id;
}
