/**
 * AC-432: operator_loop is now a fully runnable family.
 *
 * Tests verify that operator_loop can be created and executed end-to-end,
 * with proper escalation judgment evaluation.
 */

import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it, expect } from "vitest";
import { OperatorLoopCreator } from "../src/scenarios/operator-loop-creator.js";
import { generateOperatorLoopSource } from "../src/scenarios/codegen/operator-loop-codegen.js";
import { detectScenarioFamily } from "../src/scenarios/scenario-creator.js";
import { hasPipeline } from "../src/scenarios/family-pipeline.js";
import { isOperatorLoop } from "../src/scenarios/family-interfaces.js";
import { OPERATOR_LOOP_SPEC_END, OPERATOR_LOOP_SPEC_START } from "../src/scenarios/operator-loop-designer.js";

// ---------------------------------------------------------------------------
// Family infrastructure
// ---------------------------------------------------------------------------

describe("operator_loop family infrastructure", () => {
  it("family-pipeline has operator_loop registered for spec validation", () => {
    expect(hasPipeline("operator_loop")).toBe(true);
  });

  it("family-interfaces has operator_loop type guard", () => {
    expect(typeof isOperatorLoop).toBe("function");
    expect(isOperatorLoop({})).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Codegen generates valid, executable source
// ---------------------------------------------------------------------------

describe("operator_loop codegen", () => {
  const spec = {
    description: "Test escalation judgment in a deployment pipeline",
    environment_description: "Production deployment environment",
    initial_state_description: "Deployment pending",
    escalation_policy: { escalation_threshold: "high", max_escalations: 3 },
    success_criteria: ["correct escalation judgment"],
    failure_modes: ["over-escalation", "missed escalation"],
    max_steps: 15,
    actions: [
      { name: "check_logs", description: "Check deployment logs", parameters: {}, preconditions: [], effects: ["logs_checked"] },
      { name: "run_tests", description: "Run test suite", parameters: {}, preconditions: ["check_logs"], effects: ["tests_passed"] },
      { name: "deploy", description: "Deploy to production", parameters: {}, preconditions: ["run_tests"], effects: ["deployed"] },
    ],
  };

  it("generates valid JS source with all required methods", () => {
    const source = generateOperatorLoopSource(spec, "deploy_judgment");
    // Simulation base methods
    expect(source).toContain("describeScenario");
    expect(source).toContain("initialState");
    expect(source).toContain("executeAction");
    expect(source).toContain("isTerminal");
    expect(source).toContain("getResult");
    // Operator-loop specific methods
    expect(source).toContain("getEscalationLog");
    expect(source).toContain("getClarificationLog");
    expect(source).toContain("escalate");
    expect(source).toContain("requestClarification");
    expect(source).toContain("evaluateJudgment");
    expect(source).toContain("module.exports");
  });

  it("generated source is syntactically valid JS", () => {
    const source = generateOperatorLoopSource(spec, "deploy_judgment");
    new Function(source); // should not throw
  });

  it("generated scenario can be evaluated via eval", () => {
    const source = generateOperatorLoopSource(spec, "escalation_test");

    const module = { exports: {} as Record<string, unknown> };
    new Function("module", "exports", source)(module, module.exports);
    const scenario = (module.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    // Test core methods
    expect(scenario.describeScenario()).toContain("escalation judgment");

    const state = scenario.initialState(42) as Record<string, unknown>;
    expect(state.escalationLog).toEqual([]);
    expect(state.clarificationLog).toEqual([]);
    expect(state.autonomousActions).toBe(0);

    // Execute an autonomous action
    const r1 = scenario.executeAction(state, { name: "check_logs", parameters: {} }) as {
      result: { success: boolean }; state: Record<string, unknown>;
    };
    expect(r1.result.success).toBe(true);
    expect(r1.state.autonomousActions).toBe(1);

    // Escalate
    const escalated = scenario.escalate(r1.state, {
      reason: "unusual log patterns", severity: "high",
      wasNecessary: true, step: 2, context: "suspicious errors",
    }) as Record<string, unknown>;
    expect((escalated.escalationLog as unknown[]).length).toBe(1);

    // Request clarification
    const clarified = scenario.requestClarification(escalated, {
      question: "Should we proceed?", context: "errors detected", urgency: "high",
    }) as Record<string, unknown>;
    expect((clarified.clarificationLog as unknown[]).length).toBe(1);

    // Evaluate judgment
    const judgment = scenario.evaluateJudgment(clarified) as {
      score: number; dimensionScores: Record<string, number>;
      escalations: number; necessaryEscalations: number;
      clarificationsRequested: number;
    };
    expect(judgment.score).toBeGreaterThan(0);
    expect(judgment.score).toBeLessThanOrEqual(1);
    expect(judgment.escalations).toBe(1);
    expect(judgment.necessaryEscalations).toBe(1);
    expect(judgment.clarificationsRequested).toBe(1);
    expect(judgment.dimensionScores.escalationPrecision).toBe(1); // 1/1 necessary
  });

  it("scores unnecessary escalations lower", () => {
    const source = generateOperatorLoopSource(spec, "scoring_test");

    const module = { exports: {} as Record<string, unknown> };
    new Function("module", "exports", source)(module, module.exports);
    const scenario = (module.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    const state = scenario.initialState(0) as Record<string, unknown>;

    // Escalate unnecessarily
    const escalated = scenario.escalate(state, {
      reason: "just in case", severity: "low",
      wasNecessary: false, step: 1, context: "nothing wrong",
    }) as Record<string, unknown>;

    const judgment = scenario.evaluateJudgment(escalated) as {
      score: number; dimensionScores: Record<string, number>;
      unnecessaryEscalations: number;
    };
    expect(judgment.unnecessaryEscalations).toBe(1);
    expect(judgment.dimensionScores.escalationPrecision).toBe(0); // 0/1 necessary
  });

  it("enforces preconditions like other simulation families", () => {
    const source = generateOperatorLoopSource(spec, "precondition_test");

    const module = { exports: {} as Record<string, unknown> };
    new Function("module", "exports", source)(module, module.exports);
    const scenario = (module.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    const state = scenario.initialState(0) as Record<string, unknown>;

    // Try to deploy without prerequisites
    const result = scenario.executeAction(state, { name: "deploy", parameters: {} }) as {
      result: { success: boolean; error: string }; state: Record<string, unknown>;
    };
    expect(result.result.success).toBe(false);
    expect(result.result.error).toContain("precondition");
    // Failed actions should be tracked as situations requiring escalation
    expect((result.state.situationsRequiringEscalation as unknown[]).length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Creator wiring
// ---------------------------------------------------------------------------

describe("operator_loop creator", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    while (tempDirs.length > 0) {
      rmSync(tempDirs.pop()!, { recursive: true, force: true });
    }
  });

  it("persists a runnable operator_loop artifact", async () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "operator-loop-creator-"));
    tempDirs.push(knowledgeRoot);
    const mockProvider = {
      complete: async () => ({
        text: [
          OPERATOR_LOOP_SPEC_START,
          JSON.stringify({
            description: "Support triage with escalation judgment",
            environment_description: "Customer support queue",
            initial_state_description: "Open tickets waiting",
            escalation_policy: { escalation_threshold: "high", max_escalations: 3 },
            success_criteria: ["Escalate risky tickets", "Handle safe tickets autonomously"],
            failure_modes: ["missed escalation", "unnecessary escalation"],
            max_steps: 8,
            actions: [
              {
                name: "triage_ticket",
                description: "Triage the next support ticket",
                parameters: {},
                preconditions: [],
                effects: ["triaged"],
              },
              {
                name: "reply_customer",
                description: "Reply to the customer with the next action",
                parameters: {},
                preconditions: ["triage_ticket"],
                effects: ["replied"],
              },
            ],
          }),
          OPERATOR_LOOP_SPEC_END,
        ].join("\n"),
      }),
      defaultModel: () => "test-model",
    } as never;

    const creator = new OperatorLoopCreator({
      provider: mockProvider,
      knowledgeRoot,
    });
    const scenario = await creator.create(
      "Create an operator-in-the-loop scenario for support triage with escalation judgment",
      "support_triage_operator_loop",
    );

    expect(scenario.family).toBe("operator_loop");
    expect(typeof scenario.generatedSource).toBe("string");

    const scenarioDir = join(knowledgeRoot, "_custom_scenarios", "support_triage_operator_loop");
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe("operator_loop");
    expect(JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")).escalation_policy.max_escalations).toBe(3);
    expect(readFileSync(join(scenarioDir, "scenario.js"), "utf-8")).toContain("module.exports = { scenario }");
  });
});
