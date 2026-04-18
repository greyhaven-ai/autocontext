// Emit-pr orchestrator (§9.1).
//
// Pipeline:
//   1. load Artifact + latest EvalRun from the registry
//   2. resolve baseline (auto → getActive, explicit id → loadArtifact,
//      "none" / null → skip)
//   3. load or compute PromotionDecision (decidePromotion with default
//      thresholds if no decision was pre-attached)
//   4. preflight → bail on first failure set
//   5. render patches + PR body (deterministic, given the timestamp input)
//   6. dispatch on the resolved mode
//   7. return EmitResult
//
// Idempotence: every Date.now() call is replaced by the caller-supplied
// `timestamp` value threaded through the pipeline. Same inputs → byte-identical
// output files + EmitResult.

import { join } from "node:path";
import type { ArtifactId } from "../contract/branded-ids.js";
import type { Artifact, EvalRun, Patch, PromotionDecision } from "../contract/types.js";
import type { Registry } from "../registry/index.js";
import { artifactDirectory } from "../registry/artifact-store.js";
import { decidePromotion, defaultThresholds } from "../promotion/index.js";
import { renderPatches } from "./patch-renderer.js";
import { renderPrBody } from "./pr-body-renderer.js";
import { branchNameFor } from "./branch-namer.js";
import { preflight, type PreflightIssue, type PreflightDetector } from "./preflight.js";
import { loadWorkspaceLayout, type WorkspaceLayout } from "./workspace-layout.js";
import { runPatchOnlyMode } from "./modes/patch-only.js";
import { runGitMode } from "./modes/git.js";
import { runGhMode } from "./modes/gh.js";
import { resolveAutoMode, type AutoDetector } from "./modes/auto.js";

export type EmitMode = "auto" | "gh" | "git" | "patch-only";

export interface EmitPrOptions {
  /** Desired mode. Default: "auto". */
  readonly mode?: EmitMode;
  /** Alias for --mode=patch-only; wins over `mode` if both are set. */
  readonly dryRun?: boolean;
  /**
   * Explicit baseline artifact id. "auto" (default) resolves via the state
   * pointer; pass `null` to force "no incumbent" semantics.
   */
  readonly baseline?: ArtifactId | "auto" | null;
  /** Git base branch for git/gh modes. Default: "main". */
  readonly baseBranch?: string;
  /** Override the auto-generated branch name. */
  readonly branchName?: string;
  /** Override the auto-generated PR title (gh mode only). */
  readonly prTitle?: string;
  /** Layout override; default discovers via loadWorkspaceLayout. */
  readonly layout?: WorkspaceLayout;
  /** ISO-8601 timestamp threaded through for determinism. REQUIRED for idempotence tests. */
  readonly timestamp: string;
  /** autocontext version string for the PR audit footer. */
  readonly autocontextVersion: string;
  /** Working tree root (if different from the registry cwd). Default: registry.cwd. */
  readonly workingTreeRoot?: string;
  /** Optional dependency-injected detectors for preflight and auto-resolution. */
  readonly preflightDetector?: PreflightDetector;
  readonly autoDetect?: AutoDetector;
  /** Optional env for git/gh subprocess isolation. */
  readonly env?: NodeJS.ProcessEnv;
  /**
   * If true, and no PromotionDecision has been pre-computed, re-run the pure
   * `decidePromotion` with default thresholds. Default: true.
   */
  readonly computeDecisionIfMissing?: boolean;
}

export interface EmitLocationPrUrl {
  readonly kind: "pr-url";
  readonly value: string;
}
export interface EmitLocationBranch {
  readonly kind: "branch";
  readonly value: string;
}
export interface EmitLocationLocalPath {
  readonly kind: "local-path";
  readonly value: string;
}
export type EmitLocation = EmitLocationPrUrl | EmitLocationBranch | EmitLocationLocalPath;

export interface EmitResult {
  readonly mode: Exclude<EmitMode, "auto">;
  readonly resolvedMode: Exclude<EmitMode, "auto">;
  readonly branchName: string;
  readonly patches: readonly Patch[];
  readonly prBody: string;
  readonly location: EmitLocation;
  readonly timestamp: string;
  readonly preflightIssues: readonly PreflightIssue[];
  readonly decision: PromotionDecision;
}

/**
 * Main entry point for PR emission. See file header for the pipeline
 * overview. Throws on preflight failure so CI callers can surface the
 * aggregated issue list.
 */
export async function emitPr(
  registry: Registry,
  candidateId: ArtifactId,
  opts: EmitPrOptions,
): Promise<EmitResult> {
  // Resolve the final mode.
  const desiredMode: EmitMode = opts.dryRun ? "patch-only" : (opts.mode ?? "auto");
  let resolvedMode: Exclude<EmitMode, "auto">;
  let modeEcho = "";
  if (desiredMode === "auto") {
    const res = resolveAutoMode({
      cwd: registry.cwd,
      ...(opts.autoDetect ? { detect: opts.autoDetect } : {}),
    });
    resolvedMode = res.mode;
    modeEcho = res.reason;
  } else {
    resolvedMode = desiredMode;
    modeEcho = `explicit mode: ${resolvedMode}`;
  }

  // Echo resolved mode to stderr (§9.6 — never silent).
  process.stderr.write(`autoctx emit-pr: ${modeEcho}\n`);

  // 1. Load candidate + latest EvalRun.
  const candidate = registry.loadArtifact(candidateId);
  if (candidate.evalRuns.length === 0) {
    const issues: PreflightIssue[] = [
      { code: 14, message: `Candidate ${candidateId} has no EvalRun attached.` },
    ];
    throw new EmitPreflightError(issues);
  }
  const candidateEvalRef = candidate.evalRuns[candidate.evalRuns.length - 1]!;
  const candidateEvalRun: EvalRun = registry.loadEvalRun(candidateId, candidateEvalRef.evalRunId);

  // 2. Resolve baseline.
  const baseline: { artifact: Artifact; evalRun: EvalRun } | null = resolveBaseline(
    registry,
    candidate,
    opts.baseline ?? "auto",
  );

  // 3. Compute decision.
  const decision: PromotionDecision = decidePromotion({
    candidate: { artifact: candidate, evalRun: candidateEvalRun },
    baseline,
    thresholds: defaultThresholds(),
    evaluatedAt: opts.timestamp,
  });

  // 4. Preflight.
  const layout = opts.layout ?? loadWorkspaceLayout(registry.cwd);
  const preflightResult = preflight({
    registry,
    candidate,
    mode: resolvedMode,
    cwd: registry.cwd,
    layout,
    ...(opts.baseBranch ? { baseBranch: opts.baseBranch } : {}),
    ...(opts.preflightDetector ? { detect: opts.preflightDetector } : {}),
  });
  if (!preflightResult.ok) {
    throw new EmitPreflightError(preflightResult.issues);
  }

  // 5. Render patches + PR body.
  const workingTreeRoot = opts.workingTreeRoot ?? registry.cwd;
  const candidatePayloadDir = join(artifactDirectory(registry.cwd, candidate.id), "payload");
  const patches = renderPatches({
    candidate,
    baseline: baseline?.artifact ?? null,
    candidatePayloadDir,
    workingTreeRoot,
    layout,
  });
  const prBody = renderPrBody({
    candidate,
    baseline: baseline?.artifact ?? null,
    decision,
    evalRun: candidateEvalRun,
    autocontextVersion: opts.autocontextVersion,
    timestamp: opts.timestamp,
  });

  // 6. Dispatch.
  const branchName = opts.branchName ?? branchNameFor(candidate);
  const decisionBand = decisionBandOf(decision);
  const baseBranch = opts.baseBranch ?? "main";
  const prTitle = opts.prTitle
    ?? `autocontext: promote ${candidate.actuatorType} for ${candidate.scenario} (${decisionBand})`;

  let location: EmitLocation;
  switch (resolvedMode) {
    case "patch-only": {
      const path = await runPatchOnlyMode({
        cwd: registry.cwd,
        candidateId: candidate.id,
        timestamp: opts.timestamp,
        patches,
        prBody,
        decision,
        layout,
        resolvedMode: "patch-only",
        preflightIssues: preflightResult.issues,
        branchName,
      });
      location = { kind: "local-path", value: path };
      break;
    }
    case "git": {
      const res = await runGitMode({
        cwd: workingTreeRoot,
        branchName,
        baseBranch,
        patches,
        prBody,
        candidateId: candidate.id,
        decisionBand,
        ...(opts.env ? { env: opts.env } : {}),
      });
      location = { kind: "branch", value: res.branchName };
      break;
    }
    case "gh": {
      const res = await runGhMode({
        cwd: workingTreeRoot,
        branchName,
        baseBranch,
        patches,
        prBody,
        prTitle,
        candidateId: candidate.id,
        decisionBand,
        ...(opts.env ? { env: opts.env } : {}),
      });
      location = { kind: "pr-url", value: res.prUrl };
      break;
    }
  }

  return {
    mode: resolvedMode,
    resolvedMode,
    branchName,
    patches,
    prBody,
    location,
    timestamp: opts.timestamp,
    preflightIssues: preflightResult.issues,
    decision,
  };
}

// ---------- Helpers ----------

function resolveBaseline(
  registry: Registry,
  candidate: Artifact,
  arg: ArtifactId | "auto" | null,
): { artifact: Artifact; evalRun: EvalRun } | null {
  if (arg === null) return null;
  let baseArtifact: Artifact | null = null;
  if (arg === "auto") {
    baseArtifact = registry.getActive(candidate.scenario, candidate.actuatorType, candidate.environmentTag);
  } else {
    try {
      baseArtifact = registry.loadArtifact(arg);
    } catch {
      baseArtifact = null;
    }
  }
  if (baseArtifact === null || baseArtifact.evalRuns.length === 0) return null;
  const ref = baseArtifact.evalRuns[baseArtifact.evalRuns.length - 1]!;
  try {
    const ev = registry.loadEvalRun(baseArtifact.id, ref.evalRunId);
    return { artifact: baseArtifact, evalRun: ev };
  } catch {
    return null;
  }
}

function decisionBandOf(d: PromotionDecision): string {
  if (!d.pass) return "HARD FAIL";
  switch (d.recommendedTargetState) {
    case "active": return "STRONG";
    case "canary": return "MODERATE";
    case "shadow": return "MARGINAL";
    case "disabled": return "HARD FAIL";
  }
}

/**
 * Thrown when preflight aggregates ≥1 issue. Carries `issues` so the caller
 * (the CLI) can map the highest-priority code to an exit code.
 */
export class EmitPreflightError extends Error {
  readonly issues: readonly PreflightIssue[];

  constructor(issues: readonly PreflightIssue[]) {
    super(`preflight failed: ${issues.map((i) => `[${i.code}] ${i.message}`).join("; ")}`);
    this.name = "EmitPreflightError";
    this.issues = issues;
  }
}
