import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { GenerationRecovery } from "../src/loop/generation-recovery.js";
import { StagnationDetector } from "../src/loop/stagnation.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-generation-recovery-"));
}

describe("GenerationRecovery", () => {
  it("records rollback dead ends and emits regression signals", () => {
    const dir = makeTempDir();
    try {
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });
      const recovery = new GenerationRecovery({
        artifacts,
        scenarioName: "linear_outage_escalation",
        deadEndTrackingEnabled: true,
        deadEndMaxEntries: 5,
        stagnationResetEnabled: false,
        stagnationDistillTopLessons: 3,
        stagnationDetector: new StagnationDetector(),
      });

      const outcome = recovery.handleAttempt("run-1", {
        generation: 2,
        gateDecision: "rollback",
        bestScore: 0.2,
        strategy: { escalation_readiness: 0.9 },
        previousBestForGeneration: 0.5,
      });

      expect(outcome.deadEndRecorded).toBe(true);
      expect(outcome.shouldNotifyRegression).toBe(true);
      expect(outcome.events.some((event: { event: string }) => event.event === "dead_end_recorded")).toBe(true);
      expect(artifacts.readDeadEnds("linear_outage_escalation")).toContain("### Dead End");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("generates fresh-start hints when stagnation is detected", () => {
    const dir = makeTempDir();
    try {
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });
      artifacts.writePlaybook(
        "linear_outage_escalation",
        [
          "<!-- PLAYBOOK_START -->",
          "## Strategy Updates",
          "- Stay concise.",
          "<!-- PLAYBOOK_END -->",
          "<!-- LESSONS_START -->",
          "- Ask about customer impact first.",
          "- Escalate when broad impact is confirmed.",
          "<!-- LESSONS_END -->",
          "<!-- COMPETITOR_HINTS_START -->",
          "- Keep messages short.",
          "<!-- COMPETITOR_HINTS_END -->",
        ].join("\n"),
      );
      artifacts.appendDeadEnd("linear_outage_escalation", "avoid repeated over-escalation");

      const recovery = new GenerationRecovery({
        artifacts,
        scenarioName: "linear_outage_escalation",
        deadEndTrackingEnabled: false,
        deadEndMaxEntries: 5,
        stagnationResetEnabled: true,
        stagnationDistillTopLessons: 2,
        stagnationDetector: new StagnationDetector({
          rollbackThreshold: 10,
          plateauWindow: 2,
          plateauEpsilon: 0.0001,
        }),
      });

      recovery.handleAttempt("run-2", {
        generation: 1,
        gateDecision: "advance",
        bestScore: 0.4,
        strategy: { escalation_readiness: 0.4 },
        previousBestForGeneration: 0.1,
      });
      const outcome = recovery.handleAttempt("run-2", {
        generation: 2,
        gateDecision: "advance",
        bestScore: 0.4,
        strategy: { escalation_readiness: 0.41 },
        previousBestForGeneration: 0.4,
      });

      expect(outcome.freshStartHint).toContain("Stagnation detected");
      expect(outcome.freshStartHint).toContain("Ask about customer impact first.");
      expect(outcome.events.some((event: { event: string }) => event.event === "fresh_start")).toBe(true);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
