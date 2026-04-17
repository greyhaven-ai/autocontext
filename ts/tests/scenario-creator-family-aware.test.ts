import { describe, expect, it, vi } from "vitest";

import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";
import {
  OPERATOR_LOOP_SPEC_END,
  OPERATOR_LOOP_SPEC_START,
} from "../src/scenarios/operator-loop-designer.js";

describe("createScenarioFromDescription family-aware routing", () => {
  it("uses the operator-loop designer for operator_loop descriptions", async () => {
    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("produce an OperatorLoopSpec JSON")) {
          return {
            text: [
              OPERATOR_LOOP_SPEC_START,
              JSON.stringify(
                {
                  description: "Support escalation workflow",
                  environment_description: "Support case queue with protected actions",
                  initial_state_description: "A customer asks to change a payout destination",
                  escalation_policy: {
                    escalation_threshold: "high_risk_or_policy_exception",
                    max_escalations: 2,
                  },
                  success_criteria: [
                    "Escalate protected payout changes before execution",
                    "Continue after operator guidance",
                  ],
                  failure_modes: ["protected action executed without escalation"],
                  max_steps: 8,
                  actions: [
                    {
                      name: "review_request",
                      description: "Review the incoming support request",
                      parameters: {},
                      preconditions: [],
                      effects: ["request_classified"],
                    },
                    {
                      name: "escalate_to_human_operator",
                      description: "Escalate protected payout changes",
                      parameters: {},
                      preconditions: ["review_request"],
                      effects: ["operator_review_requested"],
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
            name: "support_escalation_workflow",
            taskPrompt: "Handle support escalations safely.",
            rubric: "Escalate protected actions.",
            description: "Fallback generic scenario output",
          }),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "Create an operator-loop customer support scenario where payout destination changes require human approval.",
      provider as never,
    );

    expect(provider.complete).toHaveBeenCalledWith(
      expect.objectContaining({
        systemPrompt: expect.stringContaining("produce an OperatorLoopSpec JSON"),
      }),
    );
    expect(created.family).toBe("operator_loop");
    expect(created.spec.description).toBe("Support escalation workflow");
    expect(created.spec.actions).toEqual([
      expect.objectContaining({ name: "review_request" }),
      expect.objectContaining({ name: "escalate_to_human_operator" }),
    ]);
    expect(created.spec.escalation_policy).toEqual({
      escalation_threshold: "high_risk_or_policy_exception",
      max_escalations: 2,
    });
  });

  it("falls back to agent_task when family-aware simulation creation degrades to a core-only generic spec", async () => {
    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("produce a SimulationSpec JSON")) {
          return {
            text: JSON.stringify({
              family: "simulation",
              name: "geopolitical_crisis_simulation",
              taskPrompt: "Coordinate the crisis response.",
              rubric: "Prioritize de-escalation and clear reasoning.",
              description: "A bare fallback payload without simulation actions.",
            }),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }

        return {
          text: JSON.stringify({
            family: "simulation",
            name: "geopolitical_crisis_simulation",
            taskPrompt: "Coordinate the crisis response.",
            rubric: "Prioritize de-escalation and clear reasoning.",
            description: "A bare fallback payload without simulation actions.",
          }),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "Create a geopolitical crisis simulation where a national security advisor manages an escalating international crisis using diplomatic, economic, military, intelligence, public communication, alliance, UN, and cyber actions under hidden adversary intentions and escalation thresholds.",
      provider as never,
    );

    expect(created.family).toBe("agent_task");
    expect(created.spec.taskPrompt).toBe("Coordinate the crisis response.");
    expect(created.spec).not.toHaveProperty("actions");
  });
});
