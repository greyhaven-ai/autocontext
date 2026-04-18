// patch-only mode — write a dry-run bundle to
// <cwd>/.autocontext/dry-run-patches/<candidateId>/<timestamp>/ per spec §9.5.
//
// No git operations. Deterministic output: given the same inputs (including
// the timestamp) the bundle is byte-identical across invocations.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { ArtifactId } from "../../contract/branded-ids.js";
import type { Patch, PromotionDecision } from "../../contract/types.js";
import { canonicalJsonStringify } from "../../contract/canonical-json.js";
import type { WorkspaceLayout } from "../workspace-layout.js";
import type { PreflightIssue } from "../preflight.js";

export interface PatchOnlyInputs {
  readonly cwd: string;
  readonly candidateId: ArtifactId;
  readonly timestamp: string;
  readonly patches: readonly Patch[];
  readonly prBody: string;
  readonly decision: PromotionDecision;
  readonly layout: WorkspaceLayout;
  readonly resolvedMode: "patch-only" | "git" | "gh";
  readonly preflightIssues: readonly PreflightIssue[];
  readonly branchName: string;
}

/**
 * Write the dry-run bundle to disk. Returns the absolute path of the root
 * directory it wrote.
 */
export async function runPatchOnlyMode(inputs: PatchOnlyInputs): Promise<string> {
  const stamp = safeTimestamp(inputs.timestamp);
  const root = join(
    inputs.cwd,
    ".autocontext",
    "dry-run-patches",
    inputs.candidateId,
    stamp,
  );
  mkdirSync(root, { recursive: true });

  // patches/<n>.<flattened-targetPath>.patch
  const patchesDir = join(root, "patches");
  mkdirSync(patchesDir, { recursive: true });
  for (let i = 0; i < inputs.patches.length; i++) {
    const p = inputs.patches[i]!;
    const flat = flattenPath(p.filePath);
    writeFileSync(join(patchesDir, `${i}.${flat}.patch`), p.unifiedDiff, "utf-8");
  }

  // pr-body.md
  writeFileSync(join(root, "pr-body.md"), inputs.prBody, "utf-8");

  // decision.json — canonical-JSON encoding so bytes are stable.
  writeFileSync(
    join(root, "decision.json"),
    canonicalJsonStringify(inputs.decision),
    "utf-8",
  );

  // resolved-layout.json — string-valued layout fields (the scenarioDir
  // function is serialized by capturing a pair of sample invocations so a
  // reader can reconstruct the template without the runtime).
  const resolvedLayout = serializeLayout(inputs.layout);
  writeFileSync(
    join(root, "resolved-layout.json"),
    canonicalJsonStringify(resolvedLayout),
    "utf-8",
  );

  // plan.json — the chosen mode, resolved branch, patch summary, preflight.
  const plan = {
    mode: inputs.resolvedMode,
    branchName: inputs.branchName,
    candidateId: inputs.candidateId,
    timestamp: inputs.timestamp,
    patches: inputs.patches.map((p) => ({
      filePath: p.filePath,
      operation: p.operation,
    })),
    preflightIssues: inputs.preflightIssues.map((i) => ({
      code: i.code,
      message: i.message,
    })),
  };
  writeFileSync(join(root, "plan.json"), canonicalJsonStringify(plan), "utf-8");

  return root;
}

/** Make an ISO-8601 timestamp safe for use as a path component. */
function safeTimestamp(iso: string): string {
  return iso.replace(/[:.]/g, "-");
}

/** Flatten a POSIX path into a single filename component by replacing `/`. */
function flattenPath(p: string): string {
  return p.replace(/\//g, "_");
}

/** Serialize the WorkspaceLayout into a JSON-friendly record for audit.
 *  The scenarioDir closure is not serialized — consumers who need to capture
 *  its template should pass a WorkspaceLayout built from loadWorkspaceLayout
 *  and inspect the on-disk .autocontext/workspace.json directly. */
function serializeLayout(layout: WorkspaceLayout): Record<string, string> {
  return {
    promptSubdir: layout.promptSubdir,
    policySubdir: layout.policySubdir,
    routingSubdir: layout.routingSubdir,
    modelPointerSubdir: layout.modelPointerSubdir,
  };
}
