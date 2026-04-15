import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { materializeScenario } from "../src/scenarios/materialize.js";
import {
  SCHEMA_EVOLUTION_SPEC_END,
  SCHEMA_EVOLUTION_SPEC_START,
} from "../src/scenarios/schema-evolution-designer.js";
import { SIM_SPEC_END, SIM_SPEC_START } from "../src/scenarios/simulation-designer.js";
import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";
import { WORKFLOW_SPEC_END, WORKFLOW_SPEC_START } from "../src/scenarios/workflow-designer.js";

type StressCase = {
  issueId: string;
  prompt: string;
  expectedFamily: "schema_evolution" | "simulation" | "workflow";
  expectedPromptFragment: string;
  responseText: string;
  assertPersistedSpec: (spec: Record<string, unknown>) => void;
  assertGeneratedScenario?: (source: string) => void;
};

type GeneratedScenario = {
  initialState(seed?: number): Record<string, unknown>;
  executeAction(
    state: Record<string, unknown>,
    action: { name: string; parameters: Record<string, unknown> },
  ): {
    result: { success: boolean; sideEffects?: unknown[] };
    state: Record<string, unknown>;
  };
  executeCompensation(
    state: Record<string, unknown>,
    stepName: string,
  ): {
    result?: { success: boolean };
    success?: boolean;
    error?: string;
    state?: Record<string, unknown>;
  };
  getSideEffects(state: Record<string, unknown>): unknown[];
};

function loadGeneratedScenario(source: string): GeneratedScenario {
  const module = { exports: {} as { scenario?: GeneratedScenario } };
  new Function("module", "exports", source)(module, module.exports);
  if (!module.exports.scenario) {
    throw new Error("generated scenario did not export scenario");
  }
  return module.exports.scenario;
}

const STRESS_CASES: StressCase[] = [
  {
    issueId: "AC-269",
    prompt:
      "Create a schema-evolution scenario for a structured-output task that starts with five required fields, then applies a breaking mutation that adds two required fields, removes one field, changes another field's type, and tests stale-assumption detection, knowledge migration, and recovery after the schema change.",
    expectedFamily: "schema_evolution",
    expectedPromptFragment: "produce a SchemaEvolutionSpec JSON",
    responseText: [
      SCHEMA_EVOLUTION_SPEC_START,
      JSON.stringify(
        {
          description: "Schema evolution recovery for a structured output task",
          environment_description:
            "A versioned API emits structured records to downstream validators.",
          initial_state_description:
            "Version one is active and downstream consumers assume the original field contract.",
          mutations: [
            {
              version: 2,
              description: "Add two new required fields for compliance tracking.",
              breaking: true,
              fields_added: ["compliance_status", "review_window"],
              fields_removed: ["legacy_status"],
              fields_modified: {
                priority: "string -> object",
              },
            },
            {
              version: 3,
              description: "Rename the operator_notes field and tighten validation.",
              breaking: true,
              fields_added: ["review_owner"],
              fields_removed: ["operator_notes"],
              fields_modified: {
                review_window: "string -> integer",
              },
            },
          ],
          success_criteria: [
            "Detect breaking mutations quickly.",
            "Discard stale assumptions before validating post-mutation records.",
          ],
          failure_modes: [
            "Continue validating against removed fields.",
            "Miss recovery after a breaking type change.",
          ],
          max_steps: 9,
          actions: [
            {
              name: "query_schema_version",
              description: "Inspect the current schema version and field contract.",
              parameters: { endpoint: "string" },
              preconditions: [],
              effects: ["schema_observed"],
            },
            {
              name: "adapt_validation_rules",
              description: "Update downstream assumptions after a breaking mutation.",
              parameters: { strategy: "string" },
              preconditions: ["query_schema_version"],
              effects: ["validation_rules_updated"],
            },
            {
              name: "revalidate_records",
              description: "Re-run validation against the new schema contract.",
              parameters: { sample_size: "number" },
              preconditions: ["adapt_validation_rules"],
              effects: ["records_revalidated"],
            },
          ],
        },
        null,
        2,
      ),
      SCHEMA_EVOLUTION_SPEC_END,
    ].join("\n"),
    assertPersistedSpec: (spec) => {
      expect(spec.scenario_type).toBe("schema_evolution");
      expect(Array.isArray(spec.mutations)).toBe(true);
      expect((spec.mutations as unknown[]).length).toBe(2);
    },
  },
  {
    issueId: "AC-274",
    prompt:
      "Create a cyber incident response simulation where an agent defends a network against an evolving attack, prioritizing containment speed, data-loss prevention, business-impact tradeoffs, evidence preservation, and root-cause identification as the attacker progresses from initial access through exfiltration.",
    expectedFamily: "simulation",
    expectedPromptFragment: "produce a SimulationSpec JSON",
    responseText: [
      SIM_SPEC_START,
      JSON.stringify(
        {
          description: "Cyber incident response under attacker progression",
          environment_description:
            "An enterprise network with endpoints, identity telemetry, alerting, and containment controls.",
          initial_state_description:
            "A suspicious endpoint alert and outbound transfer have been detected, but the attacker still has room to move.",
          success_criteria: [
            "Contain the attacker before exfiltration completes.",
            "Preserve evidence while minimizing business disruption.",
          ],
          failure_modes: [
            "Destroy evidence before containment.",
            "Allow exfiltration to complete.",
          ],
          max_steps: 8,
          actions: [
            {
              name: "triage_alerts",
              description:
                "Review alerts to identify the likely patient zero and active blast radius.",
              parameters: { time_window: "string" },
              preconditions: [],
              effects: ["initial_scope_established"],
            },
            {
              name: "preserve_host_evidence",
              description: "Capture volatile evidence before disruptive containment.",
              parameters: { host: "string" },
              preconditions: ["triage_alerts"],
              effects: ["volatile_evidence_preserved"],
            },
            {
              name: "contain_compromised_assets",
              description: "Apply targeted containment to stop lateral movement and exfiltration.",
              parameters: { strategy: "string" },
              preconditions: ["preserve_host_evidence"],
              effects: ["containment_applied"],
            },
          ],
        },
        null,
        2,
      ),
      SIM_SPEC_END,
    ].join("\n"),
    assertPersistedSpec: (spec) => {
      expect(spec.scenario_type).toBe("simulation");
      expect(Array.isArray(spec.actions)).toBe(true);
      expect((spec.actions as unknown[]).length).toBeGreaterThanOrEqual(3);
    },
  },
  {
    issueId: "AC-276",
    prompt:
      "Create a geopolitical crisis simulation where a national security advisor manages an escalating international crisis using diplomatic, economic, military, intelligence, public communication, alliance, UN, and cyber actions under hidden adversary intentions and escalation thresholds.",
    expectedFamily: "simulation",
    expectedPromptFragment: "produce a SimulationSpec JSON",
    responseText: [
      SIM_SPEC_START,
      JSON.stringify(
        {
          description: "Geopolitical crisis management under hidden adversary intentions",
          environment_description:
            "A multi-actor international crisis with military posture shifts, alliance politics, economic pressure, cyber disruptions, public narratives, and uncertain escalation thresholds.",
          initial_state_description:
            "A cross-border confrontation is intensifying, allied governments are asking for coordination, adversary intentions are partially hidden, and each move can change escalation risk.",
          success_criteria: [
            "Stabilize the confrontation without triggering uncontrolled escalation.",
            "Sequence diplomatic, economic, military, intelligence, and cyber actions coherently.",
          ],
          failure_modes: [
            "Escalate the crisis through poorly coordinated signaling.",
            "Ignore hidden adversary intentions and misread the confrontation.",
          ],
          max_steps: 10,
          actions: [
            {
              name: "update_intelligence_picture",
              description:
                "Refresh the intelligence picture to estimate adversary intent, readiness, and escalation thresholds.",
              parameters: { collection_focus: "string" },
              preconditions: [],
              effects: ["intelligence_picture_updated"],
            },
            {
              name: "open_backchannel_contact",
              description:
                "Use diplomatic outreach to clarify intent, test red lines, and create de-escalation options.",
              parameters: { counterpart: "string" },
              preconditions: ["update_intelligence_picture"],
              effects: ["backchannel_opened"],
            },
            {
              name: "synchronize_allied_response",
              description:
                "Coordinate military, economic, and public messaging options with allies and multilateral partners.",
              parameters: { coalition_goal: "string" },
              preconditions: ["open_backchannel_contact"],
              effects: ["allied_response_synchronized"],
            },
          ],
        },
        null,
        2,
      ),
      SIM_SPEC_END,
    ].join("\n"),
    assertPersistedSpec: (spec) => {
      expect(spec.scenario_type).toBe("simulation");
      expect(Array.isArray(spec.actions)).toBe(true);
      expect((spec.actions as unknown[]).length).toBeGreaterThanOrEqual(3);
    },
  },
  {
    issueId: "AC-550-workflow",
    prompt:
      "Create a transactional workflow scenario with compensation and side effects across payment capture, inventory reservation, and customer notification.",
    expectedFamily: "workflow",
    expectedPromptFragment: "produce a WorkflowSpec JSON",
    responseText: [
      WORKFLOW_SPEC_START,
      JSON.stringify(
        {
          description: "Payment workflow with reversible side effects",
          environment_description:
            "A checkout service coordinates payment, inventory, and notification systems.",
          initial_state_description:
            "No side effects have been produced and all workflow steps are pending.",
          workflow_steps: [
            {
              name: "charge_payment",
              description: "Capture the customer payment.",
              idempotent: false,
              reversible: true,
              compensation: "refund_payment",
            },
            {
              name: "reserve_inventory",
              description: "Reserve the purchased inventory.",
              idempotent: true,
              reversible: true,
              compensation: "release_inventory",
            },
          ],
          success_criteria: [
            "Complete workflow steps in order.",
            "Track side effects and expose compensation for reversible steps.",
          ],
          failure_modes: [
            "Payment captured without refund compensation.",
            "Inventory side effect is not tracked.",
          ],
          max_steps: 6,
          actions: [
            {
              name: "charge_payment",
              description: "Capture funds for the order.",
              parameters: { payment_id: "string" },
              preconditions: [],
              effects: ["payment_captured"],
            },
            {
              name: "reserve_inventory",
              description: "Reserve stock for the order.",
              parameters: { sku: "string" },
              preconditions: ["charge_payment"],
              effects: ["inventory_reserved"],
            },
          ],
        },
        null,
        2,
      ),
      WORKFLOW_SPEC_END,
    ].join("\n"),
    assertPersistedSpec: (spec) => {
      expect(spec.scenario_type).toBe("workflow");
      const steps = spec.workflow_steps as Array<Record<string, unknown>>;
      expect(steps[0]?.compensation).toBe("refund_payment");
      expect(steps[0]?.compensationAction).toBe("refund_payment");
      expect(steps[0]?.sideEffects).toEqual(["payment_captured"]);
    },
    assertGeneratedScenario: (source) => {
      const scenario = loadGeneratedScenario(source);
      const initialState = scenario.initialState(42);
      const chargeResult = scenario.executeAction(initialState, {
        name: "charge_payment",
        parameters: { payment_id: "pay_123" },
      });

      expect(chargeResult.result.success).toBe(true);
      expect(chargeResult.result.sideEffects).toContain("payment_captured");
      expect(scenario.getSideEffects(chargeResult.state)).toContain("payment_captured");

      const compensation = scenario.executeCompensation(chargeResult.state, "charge_payment");
      expect(compensation.result?.success).toBe(true);
      expect(compensation.error).toBeUndefined();
    },
  },
  {
    issueId: "AC-277",
    prompt:
      "Create a portfolio-construction-under-regime-change scenario where an agent manages allocations, risk rules, and regime assessment across low-volatility, rising-rate, and crisis market regimes with breaking mutations that test adaptation speed and quantitative recovery.",
    expectedFamily: "schema_evolution",
    expectedPromptFragment: "produce a SchemaEvolutionSpec JSON",
    responseText: [
      SCHEMA_EVOLUTION_SPEC_START,
      JSON.stringify(
        {
          description: "Portfolio adaptation across changing market regimes",
          environment_description:
            "A market simulation with macro indicators, portfolio exposures, and regime shocks.",
          initial_state_description:
            "A balanced portfolio is deployed in a low-volatility environment before a regime mutation hits.",
          mutations: [
            {
              version: 2,
              description: "Interest-rate regime flips bond-equity correlations.",
              breaking: true,
              fields_added: ["yield_curve_slope"],
              fields_removed: ["low_vol_assumption"],
              fields_modified: {
                duration_risk: "low -> elevated",
              },
            },
            {
              version: 3,
              description: "Crisis regime pushes cross-asset correlations toward one.",
              breaking: true,
              fields_added: ["liquidity_stress"],
              fields_removed: ["stable_correlation_matrix"],
              fields_modified: {
                volatility_regime: "moderate -> crisis",
              },
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
            {
              name: "tighten_risk_controls",
              description: "Apply new stop-losses and exposure limits after the rebalance.",
              parameters: { control_set: "string" },
              preconditions: ["rebalance_portfolio"],
              effects: ["risk_controls_updated"],
            },
          ],
        },
        null,
        2,
      ),
      SCHEMA_EVOLUTION_SPEC_END,
    ].join("\n"),
    assertPersistedSpec: (spec) => {
      expect(spec.scenario_type).toBe("schema_evolution");
      expect(Array.isArray(spec.actions)).toBe(true);
      expect((spec.actions as unknown[]).length).toBeGreaterThanOrEqual(3);
      expect(Array.isArray(spec.mutations)).toBe(true);
      expect((spec.mutations as unknown[]).length).toBe(2);
    },
  },
];

describe("new-scenario broader family materialization", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    while (tempDirs.length > 0) {
      rmSync(tempDirs.pop()!, { recursive: true, force: true });
    }
  });

  for (const testCase of STRESS_CASES) {
    it(`materializes ${testCase.issueId} through the ${testCase.expectedFamily} designer`, async () => {
      const knowledgeRoot = mkdtempSync(join(tmpdir(), `ac550-${testCase.issueId.toLowerCase()}-`));
      tempDirs.push(knowledgeRoot);

      const provider = {
        defaultModel: () => "mock-model",
        complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
          if (systemPrompt?.includes(testCase.expectedPromptFragment)) {
            return {
              text: testCase.responseText,
              model: "mock-model",
              usage: { inputTokens: 0, outputTokens: 0 },
            };
          }

          return {
            text: JSON.stringify({
              family: testCase.expectedFamily,
              name: `fallback_${testCase.issueId.toLowerCase()}`,
              taskPrompt: testCase.prompt,
              rubric: "Fallback generic rubric",
              description: "Fallback generic scenario output",
            }),
            model: "mock-model",
            usage: { inputTokens: 0, outputTokens: 0 },
          };
        }),
      };

      const created = await createScenarioFromDescription(testCase.prompt, provider as never);

      expect(provider.complete).toHaveBeenCalledWith(
        expect.objectContaining({
          systemPrompt: expect.stringContaining(testCase.expectedPromptFragment),
        }),
      );
      expect(created.family).toBe(testCase.expectedFamily);

      const materialized = await materializeScenario({
        name: created.name,
        family: created.family,
        spec: created.spec,
        knowledgeRoot,
      });

      expect(materialized.persisted).toBe(true);
      expect(materialized.generatedSource).toBe(true);
      expect(materialized.errors).toEqual([]);

      const persistedSpec = JSON.parse(
        readFileSync(join(knowledgeRoot, "_custom_scenarios", created.name, "spec.json"), "utf-8"),
      ) as Record<string, unknown>;
      testCase.assertPersistedSpec(persistedSpec);

      if (testCase.assertGeneratedScenario) {
        const source = readFileSync(
          join(knowledgeRoot, "_custom_scenarios", created.name, "scenario.js"),
          "utf-8",
        );
        testCase.assertGeneratedScenario(source);
      }
    });
  }
});
