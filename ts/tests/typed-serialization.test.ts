import { describe, expect, expectTypeOf, it } from "vitest";
import {
  AttributionResult,
  type AttributionResultDict,
  ComponentChange,
  type ComponentChangeDict,
  CreditAssignmentRecord,
  type CreditAssignmentRecordDict,
  GenerationChangeVector,
  type GenerationChangeVectorDict,
  summarizeCreditPatterns,
  type CreditPatternSummary,
} from "../src/analytics/credit-assignment.js";
import { serializeSkillPackage, type SerializedSkillPackageDict } from "../src/knowledge/package.js";
import { SkillPackage, type SkillPackageDict } from "../src/knowledge/skill-package.js";
import {
  buildGateDecidedPayload,
  buildRunStartedPayload,
  type GateDecidedPayload,
  type RunStartedPayload,
} from "../src/loop/generation-event-coordinator.js";
import {
  buildRoleCompletedPayload,
  type RoleCompletedPayload,
} from "../src/loop/generation-side-effect-coordinator.js";

describe("typed serialization contracts", () => {
  it("exposes explicit dict types for credit assignment records", () => {
    const component = new ComponentChange("playbook", 0.5, "changed", { source: "test" });
    const vector = new GenerationChangeVector(2, 0.25, [component], { family: "game" });
    const attribution = new AttributionResult(2, 0.25, { playbook: 0.25 }, { reviewer: "agent" });
    const record = new CreditAssignmentRecord("run_1", 2, vector, attribution, { status: "ok" });

    expectTypeOf(component.toDict()).toEqualTypeOf<ComponentChangeDict>();
    expectTypeOf(vector.toDict()).toEqualTypeOf<GenerationChangeVectorDict>();
    expectTypeOf(attribution.toDict()).toEqualTypeOf<AttributionResultDict>();
    expectTypeOf(record.toDict()).toEqualTypeOf<CreditAssignmentRecordDict>();

    expect(record.toDict()).toMatchObject({
      run_id: "run_1",
      generation: 2,
      vector: { generation: 2 },
      attribution: { total_delta: 0.25 },
    });
  });

  it("returns an explicit summary type for credit pattern rollups", () => {
    const summary = summarizeCreditPatterns([
      new CreditAssignmentRecord(
        "run_1",
        1,
        new GenerationChangeVector(
          1,
          0.4,
          [new ComponentChange("playbook", 1, "changed")],
        ),
        new AttributionResult(1, 0.4, { playbook: 0.4 }),
      ),
    ]);

    expectTypeOf(summary).toEqualTypeOf<CreditPatternSummary>();
    expect(summary.components[0]?.component).toBe("playbook");
  });

  it("exposes explicit payload types for loop event serialization", () => {
    const runStarted = buildRunStartedPayload({
      runId: "run-1",
      scenarioName: "grid_ctf",
      targetGenerations: 3,
    });
    const gateDecided = buildGateDecidedPayload("run-1", 2, "advance", 0.1, 0.05);
    const roleCompleted = buildRoleCompletedPayload("competitor", 125, {
      input_tokens: 2,
      outputTokens: 5,
    });

    expectTypeOf(runStarted).toEqualTypeOf<RunStartedPayload>();
    expectTypeOf(gateDecided).toEqualTypeOf<GateDecidedPayload>();
    expectTypeOf(roleCompleted).toEqualTypeOf<RoleCompletedPayload>();

    expect(runStarted.target_generations).toBe(3);
    expect(gateDecided.decision).toBe("advance");
    expect(roleCompleted.tokens).toBe(7);
  });

  it("exposes an explicit dict type for skill packages and serialized package payloads", () => {
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid CTF",
      description: "Test package",
      playbook: "Do the thing",
      lessons: ["Keep momentum"],
      bestStrategy: { opening: "fast" },
      bestScore: 0.91,
      bestElo: 1650,
      hints: "Think ahead",
      harness: { validate_move: "def validate(): pass" },
      taskPrompt: "Summarize the mission",
      judgeRubric: "Score clarity",
      exampleOutputs: [{ output: "Done", score: 0.8, reasoning: "Clear" }],
      outputFormat: "free_text",
      referenceContext: "Reference",
      contextPreparation: "Prepare",
      maxRounds: 2,
      qualityThreshold: 0.8,
    });

    expectTypeOf(pkg.toDict()).toEqualTypeOf<SkillPackageDict>();
    expectTypeOf(serializeSkillPackage(pkg)).toEqualTypeOf<SerializedSkillPackageDict>();

    const dict = pkg.toDict();
    expect(dict.scenario_name).toBe("grid_ctf");
    expect(dict.example_outputs?.[0]?.output).toBe("Done");

    const serialized = serializeSkillPackage(pkg);
    expect(serialized.format_version).toBe(1);
    expect(serialized.skill_markdown).toContain("# Grid CTF");
  });
});
