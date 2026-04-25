import { describe, expect, it, vi } from "vitest";

import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";
import {
  OPERATOR_LOOP_SPEC_END,
  OPERATOR_LOOP_SPEC_START,
} from "../src/scenarios/operator-loop-designer.js";
import { WORKFLOW_SPEC_END, WORKFLOW_SPEC_START } from "../src/scenarios/workflow-designer.js";

describe("createScenarioFromDescription family-aware routing", () => {
  it("uses the provider-backed LLM classifier before choosing the family-aware designer", async () => {
    const workflowSpec = {
      description: "Opaque request routed by classifier",
      environment_description: "A workflow with ordered side effects.",
      initial_state_description: "No steps have run.",
      workflow_steps: [
        {
          name: "prepare_payload",
          description: "Prepare the payload.",
          idempotent: true,
          reversible: false,
        },
        {
          name: "commit_payload",
          description: "Commit the payload.",
          idempotent: false,
          reversible: true,
          compensation: "rollback_payload",
        },
      ],
      success_criteria: ["Prepare the payload.", "Commit the payload safely."],
      failure_modes: ["Commit fails after preparation."],
      max_steps: 5,
      actions: [
        {
          name: "prepare_payload",
          description: "Prepare the payload.",
          parameters: {},
          preconditions: [],
          effects: ["payload_prepared"],
        },
        {
          name: "commit_payload",
          description: "Commit the payload.",
          parameters: {},
          preconditions: ["prepare_payload"],
          effects: ["payload_committed"],
        },
      ],
    };
    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("You classify a natural-language scenario description")) {
          return {
            text: JSON.stringify({
              family: "workflow",
              confidence: 0.83,
              rationale: "Opaque request requires ordered workflow execution",
            }),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }

        if (systemPrompt?.includes("produce a WorkflowSpec JSON")) {
          return {
            text: [WORKFLOW_SPEC_START, JSON.stringify(workflowSpec), WORKFLOW_SPEC_END].join("\n"),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }

        return {
          text: JSON.stringify({
            family: "agent_task",
            name: "generic_fallback",
            taskPrompt: "Generic fallback.",
            rubric: "Generic fallback rubric.",
            description: "Generic fallback output",
          }),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "glimmer plinth orbit vascade",
      provider as never,
    );

    expect(provider.complete).toHaveBeenCalledWith(
      expect.objectContaining({
        systemPrompt: expect.stringContaining("You classify a natural-language scenario description"),
      }),
    );
    expect(provider.complete).toHaveBeenCalledWith(
      expect.objectContaining({
        systemPrompt: expect.stringContaining("produce a WorkflowSpec JSON"),
      }),
    );
    expect(created.family).toBe("workflow");
    expect(created.llmClassifierFallbackUsed).toBe(true);
    expect(created.spec.description).toBe(workflowSpec.description);
  });

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

  it("keeps simulation family when the simulation designer returns raw JSON without delimiters", async () => {
    const simulationSpec = {
      description: "Geopolitical crisis management under hidden adversary intentions",
      environment_description:
        "A multi-actor international crisis with military posture shifts, alliance politics, economic pressure, and cyber disruptions.",
      initial_state_description:
        "A confrontation is intensifying and allied governments are asking for coordination.",
      success_criteria: [
        "Stabilize the confrontation without uncontrolled escalation.",
        "Sequence diplomatic, economic, military, and cyber actions coherently.",
      ],
      failure_modes: [
        "Escalate the crisis through poorly coordinated signaling.",
        "Ignore hidden adversary intentions and misread the confrontation.",
      ],
      max_steps: 10,
      actions: [
        {
          name: "update_intelligence_picture",
          description: "Refresh the intelligence picture.",
          parameters: { collection_focus: "string" },
          preconditions: [],
          effects: ["intelligence_picture_updated"],
        },
        {
          name: "open_backchannel_contact",
          description: "Open a diplomatic off-ramp.",
          parameters: { counterpart: "string" },
          preconditions: ["update_intelligence_picture"],
          effects: ["backchannel_opened"],
        },
      ],
    };

    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("produce a SimulationSpec JSON")) {
          return {
            text: JSON.stringify(simulationSpec),
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

    expect(created.family).toBe("simulation");
    expect(created.spec.description).toBe(simulationSpec.description);
    expect(created.spec.actions).toEqual([
      expect.objectContaining({ name: "update_intelligence_picture" }),
      expect.objectContaining({ name: "open_backchannel_contact" }),
    ]);
  });

  it("keeps schema_evolution family when the designer returns raw JSON without delimiters", async () => {
    const schemaEvolutionSpec = {
      description: "Portfolio adaptation across changing market regimes",
      environment_description:
        "A market simulation with macro indicators, portfolio exposures, and regime shocks.",
      initial_state_description:
        "A balanced portfolio is deployed before a breaking regime mutation hits.",
      mutations: [
        {
          version: 2,
          description: "Interest-rate regime flips bond-equity correlations.",
          breaking: true,
          fields_added: ["yield_curve_slope"],
          fields_removed: ["low_vol_assumption"],
          fields_modified: { duration_risk: "low -> elevated" },
        },
        {
          version: 3,
          description: "Crisis regime pushes cross-asset correlations toward one.",
          breaking: true,
          fields_added: ["liquidity_stress"],
          fields_removed: ["stable_correlation_matrix"],
          fields_modified: { volatility_regime: "moderate -> crisis" },
        },
      ],
      success_criteria: [
        "Adjust allocations before drawdown becomes severe.",
        "Restore risk-adjusted performance after each regime mutation.",
      ],
      failure_modes: [
        "Hold stale allocations after a regime break.",
        "Recover too slowly after crisis volatility spikes.",
      ],
      max_steps: 9,
      actions: [
        {
          name: "assess_regime_signals",
          description: "Review macro and volatility signals to classify the current regime.",
          parameters: { signal_window: "string" },
          preconditions: [],
          effects: ["regime_assessed"],
        },
        {
          name: "rebalance_portfolio",
          description: "Adjust the portfolio to align with the current regime outlook.",
          parameters: { allocation_model: "string" },
          preconditions: ["assess_regime_signals"],
          effects: ["portfolio_rebalanced"],
        },
      ],
    };

    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        if (systemPrompt?.includes("produce a SchemaEvolutionSpec JSON")) {
          return {
            text: JSON.stringify(schemaEvolutionSpec),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }

        return {
          text: JSON.stringify({
            family: "schema_evolution",
            name: "portfolio_regime_shift",
            taskPrompt: "Manage allocations across regime shifts.",
            rubric: "Track recovery after breaking mutations.",
            description: "A bare fallback payload without schema-evolution actions.",
          }),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "Build and run a 10-generation portfolio construction simulation using SchemaEvolutionInterface and WorldState. Each generation, the agent receives macro indicators, volatility metrics, geopolitical signals, and the current portfolio. After generation 3 apply a breaking SchemaMutation for a rate-hike regime, and after generation 6 apply a breaking SchemaMutation for a crisis regime. The agent should maintain and evolve a playbook of regime-specific investment heuristics across mutations.",
      provider as never,
    );

    expect(created.family).toBe("schema_evolution");
    expect(created.spec.description).toBe(schemaEvolutionSpec.description);
    expect(created.spec.mutations).toEqual([
      expect.objectContaining({ version: 2, breaking: true }),
      expect.objectContaining({ version: 3, breaking: true }),
    ]);
    expect(created.spec.actions).toEqual([
      expect.objectContaining({ name: "assess_regime_signals" }),
      expect.objectContaining({ name: "rebalance_portfolio" }),
    ]);
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
