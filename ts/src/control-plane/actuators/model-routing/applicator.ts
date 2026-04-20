// model-routing actuator (AC-545) — writes a models.json payload file to
// <scenarioDir>/routing/models/<artifactId>-model-routing.json.
//
// Rollback: content-revert — the baseline's payload content is written back to
// the same resolved target. (Spec §4: model-routing is config data, not a
// cascade-dependent artifact; content-revert is safe and symmetric.)
//
// DRY: wraps `_shared/single-file-applicator` + `_shared/content-revert-rollback`
// + `_shared/unified-diff-emitter` (same pattern as prompt-patch / tool-policy).

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Actuator, WorkspaceLayoutArg } from "../registry.js";
import type { Artifact, Patch } from "../../contract/types.js";
import { applySingleFile } from "../_shared/single-file-applicator.js";
import { emitUnifiedDiff } from "../_shared/unified-diff-emitter.js";
import { contentRevertRollback } from "../_shared/content-revert-rollback.js";
import {
  ModelRoutingPayloadSchema,
  MODEL_ROUTING_FILENAME,
  type ModelRoutingPayload,
} from "./schema.js";

/**
 * Subdirectory under the `routingSubdir` where model-routing configs land.
 * Chosen to match `routing-rule`'s `routing/*.json` convention while keeping
 * model-routing in a sibling `routing/models/` tree so the two actuator types
 * don't collide on disk.
 */
const MODEL_ROUTING_SUBDIR = "models";

function targetRelativePath(artifact: Artifact, layout: WorkspaceLayoutArg): string {
  const scenarioDir = layout.scenarioDir(artifact.scenario, artifact.environmentTag);
  return `${scenarioDir}/${layout.routingSubdir}/${MODEL_ROUTING_SUBDIR}/${artifact.id}-model-routing.json`;
}

export const modelRoutingActuator: Actuator<ModelRoutingPayload> = {
  parsePayload(raw: unknown): ModelRoutingPayload {
    return ModelRoutingPayloadSchema.parse(raw);
  },

  resolveTargetPath(artifact, layout): string {
    return targetRelativePath(artifact, layout);
  },

  async apply({ artifact, payloadDir, workingTreeRoot, layout }): Promise<void> {
    const rel = targetRelativePath(artifact, layout);
    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: MODEL_ROUTING_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },

  emitPatch({ artifact, payloadDir, workingTreeRoot, layout }): Patch {
    const rel = targetRelativePath(artifact, layout);
    const target = join(workingTreeRoot, rel);
    const oldContent = existsSync(target) ? readFileSync(target, "utf-8") : "";
    const newContent = readFileSync(join(payloadDir, MODEL_ROUTING_FILENAME), "utf-8");
    return emitUnifiedDiff({ filePath: rel, oldContent, newContent });
  },

  async rollback({
    candidate,
    baseline,
    baselinePayloadDir,
    workingTreeRoot,
    layout,
  }): Promise<Patch | Patch[]> {
    const rel = targetRelativePath(candidate, layout);
    return contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: MODEL_ROUTING_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },
};
