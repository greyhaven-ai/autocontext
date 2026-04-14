import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { materializeScenario } from "../src/scenarios/materialize.js";
import {
  OPERATOR_LOOP_SPEC_END,
  OPERATOR_LOOP_SPEC_START,
} from "../src/scenarios/operator-loop-designer.js";
import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";

describe("new-scenario operator-loop materialization", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    while (tempDirs.length > 0) {
      rmSync(tempDirs.pop()!, { recursive: true, force: true });
    }
  });

  it("materializes a runnable operator_loop scenario from a live-style description", async () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "ac537-operator-loop-"));
    tempDirs.push(knowledgeRoot);

    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("produce an OperatorLoopSpec JSON")) {
          return {
            text: [
              OPERATOR_LOOP_SPEC_START,
              JSON.stringify(
                {
                  description: "Operator-loop support escalation",
                  environment_description: "Support queue with protected payout operations",
                  initial_state_description: "A payout destination change request enters the queue",
                  escalation_policy: {
                    escalation_threshold: "high_risk_or_policy_exception",
                    max_escalations: 2,
                  },
                  success_criteria: [
                    "Escalate payout destination changes before execution",
                    "Resume the case after operator guidance",
                  ],
                  failure_modes: ["Protected payout change completed without operator review"],
                  max_steps: 7,
                  actions: [
                    {
                      name: "review_request",
                      description: "Review the support request",
                      parameters: {},
                      preconditions: [],
                      effects: ["request_reviewed"],
                    },
                    {
                      name: "escalate_to_human_operator",
                      description: "Request human approval for the payout change",
                      parameters: {},
                      preconditions: ["review_request"],
                      effects: ["operator_review_requested"],
                    },
                    {
                      name: "continue_with_operator_guidance",
                      description: "Apply the operator's decision",
                      parameters: {},
                      preconditions: ["escalate_to_human_operator"],
                      effects: ["case_resolved"],
                    },
                  ],
                },
                null,
                2,
              ),
              OPERATOR_LOOP_SPEC_END,
            ].join("\n"),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }

        return {
          text: JSON.stringify({
            family: "operator_loop",
            name: "broken_support_escalation",
            taskPrompt: "Handle protected support requests.",
            rubric: "Escalate when needed.",
            description: "Fallback generic scenario output",
          }),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "Create an operator-loop customer support scenario where payout destination changes require a human operator, and the AI must continue after the operator responds.",
      provider as never,
    );

    const materialized = await materializeScenario({
      name: created.name,
      family: created.family,
      spec: created.spec,
      knowledgeRoot,
    });

    expect(materialized.persisted).toBe(true);
    expect(materialized.generatedSource).toBe(true);
    expect(materialized.errors).toEqual([]);

    const scenarioDir = join(knowledgeRoot, "_custom_scenarios", created.name);
    const persistedSpec = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8"));
    expect(persistedSpec.scenario_type).toBe("operator_loop");
    expect(persistedSpec.actions).toEqual([
      expect.objectContaining({ name: "review_request" }),
      expect.objectContaining({ name: "escalate_to_human_operator" }),
      expect.objectContaining({ name: "continue_with_operator_guidance" }),
    ]);
  });
});
