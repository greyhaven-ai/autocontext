// Flow 5 (spec §10.3) — cascade-rollback refusal: rolling back a routing-rule
// while a tool-policy in the same scenario/env is still active must refuse
// with CascadeRollbackRequired.
//
// Sequence:
//   1. Register + promote a tool-policy artifact to active.
//   2. Register + promote a routing-rule artifact to active. The routing-rule
//      registers `rollback: { kind: "cascade-set", dependsOn: ["tool-policy"] }`,
//      so its rollback path is gated on the tool-policy being rolled back first.
//   3. Attempt `candidate rollback <routing-rule-id>` via the in-process CLI.
//      - exit code is non-zero (specifically EXIT.CASCADE_ROLLBACK_REQUIRED)
//      - stderr contains "CascadeRollbackRequired" and the dependent
//        tool-policy artifact id.
//      - NO state change occurred — both artifacts are still active.
//   4. Roll back the tool-policy first; THEN retry the routing-rule rollback;
//      assert it now succeeds.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { EXIT } from "../../../src/control-plane/cli/_shared/exit-codes.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
  promoteArtifact,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow5-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("Flow 5 — cascade-rollback refusal", () => {
  test(
    "rolling back a routing-rule with an active tool-policy refuses; rollback ordering: tool-policy first then routing-rule succeeds",
    async () => {
      const registry = openTestRegistry(tmp);

      // 1. tool-policy artifact, promoted to active.
      const toolPolicy = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "tool-policy",
        runId: "run_tool",
        ingestedAt: "2026-04-17T12:00:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: toolPolicy.artifact.id,
        to: "active",
        reason: "promote-tool-policy",
        timestamp: "2026-04-17T12:01:00.000Z",
      });

      // 2. routing-rule artifact, promoted to active.
      const routing = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "routing-rule",
        runId: "run_route",
        ingestedAt: "2026-04-17T12:05:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: routing.artifact.id,
        to: "active",
        reason: "promote-routing-rule",
        timestamp: "2026-04-17T12:06:00.000Z",
      });

      // Sanity: both are active.
      expect(registry.loadArtifact(toolPolicy.artifact.id).activationState).toBe("active");
      expect(registry.loadArtifact(routing.artifact.id).activationState).toBe("active");

      // 3. Attempt routing-rule rollback — must refuse with CascadeRollbackRequired.
      const refuse = await runControlPlaneCommand(
        [
          "candidate",
          "rollback",
          routing.artifact.id,
          "--reason",
          "regression x",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:10:00.000Z" },
      );
      expect(refuse.exitCode).toBe(EXIT.CASCADE_ROLLBACK_REQUIRED);
      expect(refuse.exitCode).not.toBe(0);
      expect(refuse.stderr).toContain("CascadeRollbackRequired");
      // Names the dependent tool-policy artifact id.
      expect(refuse.stderr).toContain(toolPolicy.artifact.id);

      // 4. NO state change — both still active.
      expect(registry.loadArtifact(toolPolicy.artifact.id).activationState).toBe("active");
      expect(registry.loadArtifact(routing.artifact.id).activationState).toBe("active");

      // 5. Roll back the tool-policy first; then retry the routing-rule.
      const rb1 = await runControlPlaneCommand(
        [
          "candidate",
          "rollback",
          toolPolicy.artifact.id,
          "--reason",
          "tool-policy regression",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:11:00.000Z" },
      );
      expect(rb1.exitCode).toBe(0);
      expect(registry.loadArtifact(toolPolicy.artifact.id).activationState).toBe("candidate");

      const rb2 = await runControlPlaneCommand(
        [
          "candidate",
          "rollback",
          routing.artifact.id,
          "--reason",
          "routing-rule regression",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:12:00.000Z" },
      );
      expect(rb2.exitCode).toBe(0);
      expect(registry.loadArtifact(routing.artifact.id).activationState).toBe("candidate");
    },
  );
});
