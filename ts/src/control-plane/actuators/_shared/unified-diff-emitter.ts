// Shared helper: produce a Patch whose unifiedDiff body is a standard
// unified-diff string. Used by every concrete actuator's emitPatch.

import { createTwoFilesPatch } from "diff";
import type { Patch } from "../../contract/types.js";

export interface EmitUnifiedDiffInputs {
  /** Path the patch refers to (relative or absolute — the caller's choice; written verbatim). */
  readonly filePath: string;
  /** Content currently at `filePath` in the working tree (empty string if the file is new). */
  readonly oldContent: string;
  /** Target content after the patch is applied (empty string if the file should be deleted). */
  readonly newContent: string;
}

/**
 * Compute the operation implied by the (oldContent, newContent) pair:
 *   - oldContent empty + newContent non-empty → "create"
 *   - oldContent non-empty + newContent empty → "delete"
 *   - otherwise                               → "modify"
 */
function classify(oldContent: string, newContent: string): Patch["operation"] {
  if (oldContent.length === 0 && newContent.length > 0) return "create";
  if (oldContent.length > 0 && newContent.length === 0) return "delete";
  return "modify";
}

/**
 * Build a Patch with a unified-diff body. The diff uses both old and new paths
 * set to `filePath` (we use `createTwoFilesPatch` rather than `createPatch`
 * because the former lets us omit the `Index:` line while still emitting
 * standard `---`/`+++` headers, which `applyPatch` consumes).
 */
export function emitUnifiedDiff(inputs: EmitUnifiedDiffInputs): Patch {
  const { filePath, oldContent, newContent } = inputs;
  const unifiedDiff = createTwoFilesPatch(
    filePath,
    filePath,
    oldContent,
    newContent,
    undefined,
    undefined,
  );
  const patch: Patch = {
    filePath,
    operation: classify(oldContent, newContent),
    unifiedDiff,
    afterContent: newContent,
  };
  return patch;
}
