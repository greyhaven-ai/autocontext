// fine-tuned-model actuator — writes a pointer.json payload file to
// <scenarioDir>/<modelPointerSubdir>/<artifactId>-fine-tuned-model.json.
//
// Rollback: pointer-flip. A pointer-flip rollback is a state-only change
// in its canonical interpretation — the "active" state pointer under
// .autocontext/state/active/ flips back to the baseline artifact id, and
// the on-disk pointer.json is left alone. This actuator's rollback() still
// produces a Patch describing the pointer change for the emit pipeline's
// PR body, but by construction the diff is small: only the JSON pointer
// contents are involved, never bulk model weights.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Actuator, WorkspaceLayoutArg } from "../registry.js";
import type { Artifact, Patch } from "../../contract/types.js";
import { applySingleFile } from "../_shared/single-file-applicator.js";
import { emitUnifiedDiff } from "../_shared/unified-diff-emitter.js";
import {
  FineTunedModelPayloadSchema,
  FINE_TUNED_MODEL_FILENAME,
  type FineTunedModelPayload,
} from "./schema.js";

function targetRelativePath(artifact: Artifact, layout: WorkspaceLayoutArg): string {
  const scenarioDir = layout.scenarioDir(artifact.scenario, artifact.environmentTag);
  return `${scenarioDir}/${layout.modelPointerSubdir}/${artifact.id}-fine-tuned-model.json`;
}

export const fineTunedModelActuator: Actuator<FineTunedModelPayload> = {
  parsePayload(raw: unknown): FineTunedModelPayload {
    return FineTunedModelPayloadSchema.parse(raw);
  },

  resolveTargetPath(artifact, layout): string {
    return targetRelativePath(artifact, layout);
  },

  async apply({ artifact, payloadDir, workingTreeRoot, layout }): Promise<void> {
    const rel = targetRelativePath(artifact, layout);
    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: FINE_TUNED_MODEL_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },

  emitPatch({ artifact, payloadDir, workingTreeRoot, layout }): Patch {
    const rel = targetRelativePath(artifact, layout);
    const target = join(workingTreeRoot, rel);
    const oldContent = existsSync(target) ? readFileSync(target, "utf-8") : "";
    const newContent = readFileSync(join(payloadDir, FINE_TUNED_MODEL_FILENAME), "utf-8");
    return emitUnifiedDiff({ filePath: rel, oldContent, newContent });
  },

  async rollback({
    candidate,
    baseline,
    baselinePayloadDir,
    workingTreeRoot,
    layout,
  }): Promise<Patch | Patch[]> {
    // pointer-flip: produce a descriptive Patch for the PR body, but do NOT
    // mutate the working tree — the state pointer flip is the authoritative
    // action, performed by the emit pipeline via registry.writeStatePointer.
    const candRel = targetRelativePath(candidate, layout);
    const candTarget = join(workingTreeRoot, candRel);
    const oldContent = existsSync(candTarget) ? readFileSync(candTarget, "utf-8") : "";
    const baselineFile = join(baselinePayloadDir, FINE_TUNED_MODEL_FILENAME);
    if (!existsSync(baselineFile)) {
      throw new Error(
        `fine-tuned-model rollback(${baseline.id}): baseline payload file missing`,
      );
    }
    const newContent = readFileSync(baselineFile, "utf-8");
    // We emit against the candidate's target path — the rollback notionally
    // replaces the candidate's pointer with the baseline's pointer contents.
    return emitUnifiedDiff({
      filePath: candRel,
      oldContent,
      newContent,
    });
  },
};
