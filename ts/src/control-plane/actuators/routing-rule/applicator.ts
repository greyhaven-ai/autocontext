// routing-rule actuator — writes a rule.json payload file to
// <scenarioDir>/<routingSubdir>/<artifactId>-routing-rule.json.
//
// Rollback: cascade-set (dependsOn: ["tool-policy"]). If the caller reports
// any dependents in an incompatible state (via
// `dependentsInIncompatibleState`), rollback throws `CascadeRollbackRequired`
// carrying the dependent ids; the emit pipeline's cascading-rollback loop
// consumes this signal to drive the dependent rollbacks first.
//
// When no incompatible dependents are present, rollback produces a content-
// reverting patch identical in shape to tool-policy / prompt-patch rollbacks.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Actuator, WorkspaceLayoutArg } from "../registry.js";
import type { Artifact, Patch } from "../../contract/types.js";
import type { ArtifactId } from "../../contract/branded-ids.js";
import { applySingleFile } from "../_shared/single-file-applicator.js";
import { emitUnifiedDiff } from "../_shared/unified-diff-emitter.js";
import { contentRevertRollback } from "../_shared/content-revert-rollback.js";
import { CascadeRollbackRequired } from "../errors.js";
import {
  RoutingRulePayloadSchema,
  ROUTING_RULE_FILENAME,
  type RoutingRulePayload,
} from "./schema.js";

function targetRelativePath(artifact: Artifact, layout: WorkspaceLayoutArg): string {
  const scenarioDir = layout.scenarioDir(artifact.scenario, artifact.environmentTag);
  return `${scenarioDir}/${layout.routingSubdir}/${artifact.id}-routing-rule.json`;
}

export const routingRuleActuator: Actuator<RoutingRulePayload> = {
  parsePayload(raw: unknown): RoutingRulePayload {
    return RoutingRulePayloadSchema.parse(raw);
  },

  resolveTargetPath(artifact, layout): string {
    return targetRelativePath(artifact, layout);
  },

  async apply({ artifact, payloadDir, workingTreeRoot, layout }): Promise<void> {
    const rel = targetRelativePath(artifact, layout);
    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: ROUTING_RULE_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },

  emitPatch({ artifact, payloadDir, workingTreeRoot, layout }): Patch {
    const rel = targetRelativePath(artifact, layout);
    const target = join(workingTreeRoot, rel);
    const oldContent = existsSync(target) ? readFileSync(target, "utf-8") : "";
    const newContent = readFileSync(join(payloadDir, ROUTING_RULE_FILENAME), "utf-8");
    return emitUnifiedDiff({ filePath: rel, oldContent, newContent });
  },

  async rollback({
    candidate,
    baseline,
    baselinePayloadDir,
    workingTreeRoot,
    layout,
    dependentsInIncompatibleState,
  }): Promise<Patch | Patch[]> {
    // Cascade-set semantics: if any declared dependents are still in an
    // incompatible active state, refuse to roll back and surface the ids.
    if (dependentsInIncompatibleState !== undefined && dependentsInIncompatibleState.length > 0) {
      throw new CascadeRollbackRequired(
        `routing-rule rollback for ${candidate.id} requires prior rollback of `
        + `${dependentsInIncompatibleState.length} dependent(s)`,
        dependentsInIncompatibleState,
      );
    }
    const rel = targetRelativePath(candidate, layout);
    return contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: ROUTING_RULE_FILENAME,
      resolvedTargetPath: join(workingTreeRoot, rel),
    });
  },
};
