// tool-policy actuator — writes a policy.json payload file to
// <scenarioDir>/<policySubdir>/<artifactId>-tool-policy.json.
//
// Rollback: content-revert — the baseline's payload file is written back.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Actuator, WorkspaceLayoutArg } from "../registry.js";
import type { Artifact, Patch } from "../../contract/types.js";
import { applySingleFile } from "../_shared/single-file-applicator.js";
import { emitUnifiedDiff } from "../_shared/unified-diff-emitter.js";
import { contentRevertRollback } from "../_shared/content-revert-rollback.js";
import {
  ToolPolicyPayloadSchema,
  TOOL_POLICY_FILENAME,
  type ToolPolicyPayload,
} from "./schema.js";

function targetRelativePath(artifact: Artifact, layout: WorkspaceLayoutArg): string {
  const scenarioDir = layout.scenarioDir(artifact.scenario, artifact.environmentTag);
  return `${scenarioDir}/${layout.policySubdir}/${artifact.id}-tool-policy.json`;
}

export const toolPolicyActuator: Actuator<ToolPolicyPayload> = {
  parsePayload(raw: unknown): ToolPolicyPayload {
    return ToolPolicyPayloadSchema.parse(raw);
  },

  resolveTargetPath(artifact, layout): string {
    return targetRelativePath(artifact, layout);
  },

  async apply({ artifact, payloadDir, workingTreeRoot, layout }): Promise<void> {
    const rel = targetRelativePath(artifact, layout);
    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: TOOL_POLICY_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },

  emitPatch({ artifact, payloadDir, workingTreeRoot, layout }): Patch {
    const rel = targetRelativePath(artifact, layout);
    const target = join(workingTreeRoot, rel);
    const oldContent = existsSync(target) ? readFileSync(target, "utf-8") : "";
    const newContent = readFileSync(join(payloadDir, TOOL_POLICY_FILENAME), "utf-8");
    return emitUnifiedDiff({ filePath: rel, oldContent, newContent });
  },

  async rollback({ candidate, baseline, baselinePayloadDir, workingTreeRoot, layout }): Promise<Patch | Patch[]> {
    const rel = targetRelativePath(candidate, layout);
    return contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: TOOL_POLICY_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },
};
