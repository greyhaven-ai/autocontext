// prompt-patch actuator — writes a single prompt.txt payload file to
// <scenarioDir>/<promptSubdir>/<artifactId>-prompt-patch.txt.
//
// Naming rule (documented here, not in the spec):
//   The resolved target uses the artifact id (ULID) plus the actuator type
//   suffix so multiple candidate prompts can coexist on disk without colliding.
//   After promotion to active the state pointer records which artifact id is
//   currently canonical for (scenario, actuatorType, env); consumers resolve
//   the active file via the pointer, not via the path alone.
//
// Rollback: content-revert — the baseline's payload content is written back to
// the same resolved target.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Actuator, WorkspaceLayoutArg } from "../registry.js";
import type { Artifact, Patch } from "../../contract/types.js";
import { applySingleFile } from "../_shared/single-file-applicator.js";
import { emitUnifiedDiff } from "../_shared/unified-diff-emitter.js";
import { contentRevertRollback } from "../_shared/content-revert-rollback.js";
import { PromptPatchPayloadSchema, PROMPT_PATCH_FILENAME, type PromptPatchPayload } from "./schema.js";

function targetRelativePath(artifact: Artifact, layout: WorkspaceLayoutArg): string {
  const scenarioDir = layout.scenarioDir(artifact.scenario, artifact.environmentTag);
  return `${scenarioDir}/${layout.promptSubdir}/${artifact.id}-prompt-patch.txt`;
}

export const promptPatchActuator: Actuator<PromptPatchPayload> = {
  parsePayload(raw: unknown): PromptPatchPayload {
    return PromptPatchPayloadSchema.parse(raw);
  },

  resolveTargetPath(artifact, layout): string {
    return targetRelativePath(artifact, layout);
  },

  async apply({ artifact, payloadDir, workingTreeRoot, layout }): Promise<void> {
    const rel = targetRelativePath(artifact, layout);
    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: PROMPT_PATCH_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },

  emitPatch({ artifact, payloadDir, workingTreeRoot, layout }): Patch {
    const rel = targetRelativePath(artifact, layout);
    const target = join(workingTreeRoot, rel);
    const oldContent = existsSync(target) ? readFileSync(target, "utf-8") : "";
    const newContent = readFileSync(join(payloadDir, PROMPT_PATCH_FILENAME), "utf-8");
    return emitUnifiedDiff({
      filePath: rel,
      oldContent,
      newContent,
    });
  },

  async rollback({ candidate, baseline, baselinePayloadDir, workingTreeRoot, layout }): Promise<Patch | Patch[]> {
    const rel = targetRelativePath(candidate, layout);
    const patch = contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: PROMPT_PATCH_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
    return patch;
  },
};
