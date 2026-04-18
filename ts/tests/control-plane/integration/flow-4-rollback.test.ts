// Flow 4 (spec §10.3) — rollback a promoted artifact (content-revert).
//
// Sequence:
//   1. Register A (prompt-patch), attach a passing eval, promote to active.
//   2. Register B in the SAME (scenario, actuatorType, env) tuple, attach a
//      passing eval, promote to active. The registry's
//      demote-previous-active rule transitions A from active → deprecated
//      automatically (see registry/index.ts demotePreviousActiveAndPoint).
//   3. Roll back B via `candidate rollback <B-id> --reason ...`.
//      - B transitions active → candidate
//      - A REMAINS deprecated (spec §6.1 state graph does not auto-restore
//        a deprecated artifact on rollback; this is the observed current
//        behavior — see TODO(post-v1) below).
//   4. Drive the actuator's rollback() directly to compute the content-revert
//      patch and apply it to the working tree. Assert the working-tree file
//      at B's resolved target path now contains A's payload content.
//
// TODO(post-v1): the spec is silent on whether `candidate rollback` should
// automatically restore the previously-deprecated incumbent. v1 deliberately
// does not — operators must explicitly re-promote A via `promotion apply
// <A-id> --to active --reason "restored after B rollback"`. The test asserts
// the current behavior, not the (possibly desired) auto-restore behavior.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { getActuator } from "../../../src/control-plane/actuators/registry.js";
import { defaultWorkspaceLayout } from "../../../src/control-plane/emit/workspace-layout.js";
import { artifactDirectory } from "../../../src/control-plane/registry/artifact-store.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
  promoteArtifact,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow4-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("Flow 4 — rollback (content-revert) end-to-end", () => {
  test(
    "rollback transitions B → candidate; A remains deprecated; working tree reverts to A's payload content",
    async () => {
      const registry = openTestRegistry(tmp);
      const layout = defaultWorkspaceLayout();

      // ---- 1. Register A and promote to active ----
      const builtA = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        payloadSuffix: "A",
        payload: { files: { "prompt.txt": "A: original baseline prompt\n" } },
        runId: "run_A",
        ingestedAt: "2026-04-17T12:00:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: builtA.artifact.id,
        to: "active",
        reason: "promote-A",
        timestamp: "2026-04-17T12:01:00.000Z",
      });

      // ---- 2. Register B (same group), promote to active. ----
      // The registry auto-demotes A from active → deprecated.
      const builtB = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        payloadSuffix: "B",
        payload: { files: { "prompt.txt": "B: replacement prompt\n" } },
        runId: "run_B",
        ingestedAt: "2026-04-17T12:10:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: builtB.artifact.id,
        to: "active",
        reason: "promote-B",
        timestamp: "2026-04-17T12:11:00.000Z",
      });

      // Confirm the demote-previous-active rule fired.
      const aAfter = registry.loadArtifact(builtA.artifact.id);
      const bAfter = registry.loadArtifact(builtB.artifact.id);
      expect(aAfter.activationState).toBe("deprecated");
      expect(bAfter.activationState).toBe("active");

      // Pre-populate the working tree to reflect what an emit-pr deploy would
      // have written for both A and B (each lives at its own
      // `<id>-prompt-patch.txt` path because the actuator embeds the artifact
      // id in the filename).
      const targetA = join(
        tmp,
        layout.scenarioDir(builtA.artifact.scenario, builtA.artifact.environmentTag),
        layout.promptSubdir,
        `${builtA.artifact.id}-prompt-patch.txt`,
      );
      const targetB = join(
        tmp,
        layout.scenarioDir(builtB.artifact.scenario, builtB.artifact.environmentTag),
        layout.promptSubdir,
        `${builtB.artifact.id}-prompt-patch.txt`,
      );
      mkdirSync(dirname(targetA), { recursive: true });
      mkdirSync(dirname(targetB), { recursive: true });
      writeFileSync(targetA, "A: original baseline prompt\n");
      writeFileSync(targetB, "B: replacement prompt\n");

      // ---- 3. Roll back B via the CLI. ----
      const rb = await runControlPlaneCommand(
        [
          "candidate",
          "rollback",
          builtB.artifact.id,
          "--reason",
          "regression found",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:20:00.000Z" },
      );
      expect(rb.exitCode).toBe(0);

      // 3a. B → candidate.
      const bRb = registry.loadArtifact(builtB.artifact.id);
      expect(bRb.activationState).toBe("candidate");
      // 3b. A stays deprecated (TODO(post-v1) above).
      const aRb = registry.loadArtifact(builtA.artifact.id);
      expect(aRb.activationState).toBe("deprecated");

      // ---- 4. Drive the actuator's rollback() to produce + apply the
      //         content-revert patch. The candidate is B; the baseline is A
      //         (the previously-displaced active). ----
      const reg = getActuator("prompt-patch");
      expect(reg).not.toBeNull();
      const baselinePayloadDir = join(
        artifactDirectory(tmp, builtA.artifact.id),
        "payload",
      );
      const candidatePayloadDir = join(
        artifactDirectory(tmp, builtB.artifact.id),
        "payload",
      );

      const patchOrPatches = await reg!.actuator.rollback({
        candidate: bRb,
        baseline: aRb,
        candidatePayloadDir,
        baselinePayloadDir,
        workingTreeRoot: tmp,
        layout,
      });
      const patches = Array.isArray(patchOrPatches) ? patchOrPatches : [patchOrPatches];
      expect(patches).toHaveLength(1);
      const revert = patches[0]!;
      // Apply the patch by writing afterContent to the patch's filePath
      // (mirrors what runGitMode does — the unifiedDiff is render-only).
      const absPath = revert.filePath; // contentRevertRollback emits absolute paths
      writeFileSync(absPath, revert.afterContent ?? "", "utf-8");

      // ---- 5. Verify content-revert. ----
      // The actuator's contentRevertRollback writes the baseline (A's) content
      // to the candidate (B's) resolved target path. After applying the patch,
      // B's path should contain A's content, and A's path is untouched.
      expect(existsSync(targetB)).toBe(true);
      expect(readFileSync(targetB, "utf-8")).toBe("A: original baseline prompt\n");
      expect(readFileSync(targetA, "utf-8")).toBe("A: original baseline prompt\n");
    },
  );
});
