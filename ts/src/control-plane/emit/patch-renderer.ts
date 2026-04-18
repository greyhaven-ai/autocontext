// Patch renderer — given a candidate Artifact, a baseline Artifact (or null),
// the resolved workspace layout, and the candidate's payload directory, produce
// the Patch[] that the emit pipeline will render into a PR body.
//
// V1: each actuator emits a single Patch (one affected file). The return type
// is an array so multi-file actuators can land without a signature change.
//
// Pure function: no mutation, no network, only fs reads (via the actuator's
// emitPatch — which reads the candidate's on-disk payload file and the existing
// working-tree file, if any).
//
// Import discipline (§3.2): emit/ imports actuators/; the `baseline` argument
// is accepted so future actuators can produce diffs that require baseline
// content (v1's four actuators do not need it — they diff against the working
// tree — but the signature is preserved).

import type { Artifact, Patch } from "../contract/types.js";
import { getActuator } from "../actuators/registry.js";
import type { WorkspaceLayout } from "./workspace-layout.js";

export interface RenderPatchesInputs {
  readonly candidate: Artifact;
  readonly baseline: Artifact | null;
  /** Absolute path to the candidate's on-disk payload directory. */
  readonly candidatePayloadDir: string;
  /** Absolute path to the repo root whose working tree receives the patch. */
  readonly workingTreeRoot: string;
  readonly layout: WorkspaceLayout;
}

/**
 * Render the Patch[] that represents what the candidate's actuator would do
 * to the working tree. Throws if the candidate's actuatorType is not
 * registered.
 */
export function renderPatches(inputs: RenderPatchesInputs): Patch[] {
  const { candidate, candidatePayloadDir, workingTreeRoot, layout } = inputs;
  const reg = getActuator(candidate.actuatorType);
  if (reg === null) {
    throw new Error(
      `renderPatches: no actuator registered for type '${candidate.actuatorType}' `
      + `(artifact ${candidate.id}). Ensure actuators/index.js has been imported so `
      + `the four v1 actuators are registered.`,
    );
  }
  const patch = reg.actuator.emitPatch({
    artifact: candidate,
    payloadDir: candidatePayloadDir,
    workingTreeRoot,
    layout,
  });
  return [patch];
}
