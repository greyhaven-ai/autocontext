// Shared helper: given a candidate artifact and a baseline artifact (with the
// baseline's payload on disk), produce a Patch that reverts the working-tree
// file at `resolvedTargetPath` back to the baseline's payload contents.
//
// Used by prompt-patch and tool-policy actuators whose declared rollback kind
// is "content-revert".

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Artifact, Patch } from "../../contract/types.js";
import { emitUnifiedDiff } from "./unified-diff-emitter.js";

export interface ContentRevertInputs {
  readonly candidate: Artifact;
  readonly baseline: Artifact;
  readonly baselinePayloadDir: string;
  readonly payloadFileName: string;
  /** Absolute path in the working tree where the candidate wrote its payload file. */
  readonly resolvedTargetPath: string;
}

/**
 * Build the rollback Patch. The "oldContent" for the diff is the current
 * working-tree content at `resolvedTargetPath` (empty string if the file is
 * missing); the "newContent" is the baseline's payload file contents.
 *
 * Does not touch disk beyond the two reads — the caller is responsible for
 * applying the Patch via their emit pipeline.
 */
export function contentRevertRollback(inputs: ContentRevertInputs): Patch {
  const { baseline, baselinePayloadDir, payloadFileName, resolvedTargetPath } = inputs;

  const baselineFile = join(baselinePayloadDir, payloadFileName);
  if (!existsSync(baselineFile)) {
    throw new Error(
      `contentRevertRollback(${baseline.id}): baseline payload file '${payloadFileName}' `
      + `missing from ${baselinePayloadDir}`,
    );
  }
  const baselineContent = readFileSync(baselineFile, "utf-8");

  const currentContent = existsSync(resolvedTargetPath)
    ? readFileSync(resolvedTargetPath, "utf-8")
    : "";

  return emitUnifiedDiff({
    filePath: resolvedTargetPath,
    oldContent: currentContent,
    newContent: baselineContent,
  });
}
