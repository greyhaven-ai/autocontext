/**
 * Code mission verifiers and factory (AC-415).
 *
 * Hard external verifiers that run shell commands (test, lint, build)
 * and determine mission success from exit codes.
 */

import { execFileSync } from "node:child_process";
import { z } from "zod";
import { childProcessEnvWithoutControlPlaneSecrets } from "../security/child-process-env.js";
import type { MissionManager } from "./manager.js";
import type { Mission, VerifierResult } from "./types.js";
import { MissionBudgetSchema } from "./types.js";

// ---------------------------------------------------------------------------
// Verifier interface
// ---------------------------------------------------------------------------

export interface Verifier {
  label: string;
  verify(missionId: string): Promise<VerifierResult>;
}

// ---------------------------------------------------------------------------
// CommandVerifier — runs a shell command, passes on exit 0
// ---------------------------------------------------------------------------

export class CommandVerifier implements Verifier {
  readonly label: string;
  private readonly command: string;
  private readonly cwd: string;

  constructor(command: string, cwd: string) {
    this.command = command;
    this.label = command;
    this.cwd = cwd;
  }

  async verify(_missionId: string): Promise<VerifierResult> {
    try {
      const stdout = execFileSync("/bin/sh", ["-c", this.command], {
        cwd: this.cwd,
        env: childProcessEnvWithoutControlPlaneSecrets(),
        encoding: "utf-8",
        timeout: 120_000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      return {
        passed: true,
        reason: `Command '${this.command}' passed (exit 0)`,
        suggestions: [],
        metadata: { stdout: stdout.trim(), command: this.command },
      };
    } catch (err) {
      const exitCode = (err as { status?: number }).status ?? 1;
      const stderr = (err as { stderr?: string }).stderr ?? "";
      const stdout = (err as { stdout?: string }).stdout ?? "";
      return {
        passed: false,
        reason: `Command '${this.command}' failed (exit ${exitCode})`,
        suggestions: stderr ? [`stderr: ${stderr.trim().slice(0, 500)}`] : [],
        metadata: {
          command: this.command,
          exitCode,
          stdout: stdout.trim().slice(0, 2000),
          stderr: stderr.trim().slice(0, 2000),
        },
      };
    }
  }
}

// ---------------------------------------------------------------------------
// CompositeVerifier — all verifiers must pass (short-circuit)
// ---------------------------------------------------------------------------

export class CompositeVerifier implements Verifier {
  readonly label: string;
  private readonly verifiers: Verifier[];

  constructor(verifiers: Verifier[]) {
    this.verifiers = verifiers;
    this.label = verifiers.map((v) => v.label).join(" && ");
  }

  async verify(missionId: string): Promise<VerifierResult> {
    for (const verifier of this.verifiers) {
      const result = await verifier.verify(missionId);
      if (!result.passed) {
        return {
          passed: false,
          reason: result.reason,
          suggestions: result.suggestions ?? [],
          metadata: { ...result.metadata, failedVerifier: verifier.label },
        };
      }
    }
    return {
      passed: true,
      reason: `All ${this.verifiers.length} verifier(s) passed`,
      suggestions: [],
      metadata: { verifierCount: this.verifiers.length },
    };
  }
}

// ---------------------------------------------------------------------------
// CodeMissionSpec
// ---------------------------------------------------------------------------

export const CodeMissionSpecSchema = z.object({
  name: z.string(),
  goal: z.string(),
  repoPath: z.string(),
  testCommand: z.string(),
  lintCommand: z.string().optional(),
  buildCommand: z.string().optional(),
  budget: MissionBudgetSchema.optional(),
  metadata: z.record(z.unknown()).default({}),
});

export type CodeMissionSpec = z.infer<typeof CodeMissionSpecSchema>;

function buildCodeMissionVerifier(spec: Pick<CodeMissionSpec, "repoPath" | "testCommand" | "lintCommand" | "buildCommand">): Verifier {
  const verifiers: Verifier[] = [
    new CommandVerifier(spec.testCommand, spec.repoPath),
  ];
  if (spec.lintCommand) {
    verifiers.push(new CommandVerifier(spec.lintCommand, spec.repoPath));
  }
  if (spec.buildCommand) {
    verifiers.push(new CommandVerifier(spec.buildCommand, spec.repoPath));
  }
  return verifiers.length === 1 ? verifiers[0] : new CompositeVerifier(verifiers);
}

export function attachCodeMissionVerifier(
  manager: MissionManager,
  missionId: string,
  spec: Pick<CodeMissionSpec, "repoPath" | "testCommand" | "lintCommand" | "buildCommand">,
): void {
  const verifier = buildCodeMissionVerifier(spec);
  manager.setVerifier(missionId, async (resolvedMissionId) => verifier.verify(resolvedMissionId));
}

export function rehydrateMissionVerifier(manager: MissionManager, mission: Mission): boolean {
  const metadata = mission.metadata as Record<string, unknown> | undefined;
  if (!metadata || metadata.missionType !== "code") {
    return false;
  }

  const repoPath = typeof metadata.repoPath === "string" ? metadata.repoPath : null;
  const testCommand = typeof metadata.testCommand === "string" ? metadata.testCommand : null;
  if (!repoPath || !testCommand) {
    return false;
  }

  attachCodeMissionVerifier(manager, mission.id, {
    repoPath,
    testCommand,
    lintCommand: typeof metadata.lintCommand === "string" ? metadata.lintCommand : undefined,
    buildCommand: typeof metadata.buildCommand === "string" ? metadata.buildCommand : undefined,
  });
  return true;
}

// ---------------------------------------------------------------------------
// createCodeMission — factory
// ---------------------------------------------------------------------------

export function createCodeMission(
  manager: MissionManager,
  spec: CodeMissionSpec,
): string {
  const parsed = CodeMissionSpecSchema.parse(spec);

  const id = manager.create({
    name: parsed.name,
    goal: parsed.goal,
    budget: parsed.budget,
    metadata: {
      ...parsed.metadata,
      missionType: "code",
      repoPath: parsed.repoPath,
      testCommand: parsed.testCommand,
      ...(parsed.lintCommand ? { lintCommand: parsed.lintCommand } : {}),
      ...(parsed.buildCommand ? { buildCommand: parsed.buildCommand } : {}),
    },
  });
  attachCodeMissionVerifier(manager, id, {
    repoPath: parsed.repoPath,
    testCommand: parsed.testCommand,
    lintCommand: parsed.lintCommand,
    buildCommand: parsed.buildCommand,
  });

  return id;
}
