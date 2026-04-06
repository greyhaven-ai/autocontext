/**
 * AC-440: Spec auto-heal — graceful recovery from malformed specs.
 *
 * Tests verify that the auto-heal module can detect and fix common spec
 * issues before they reach codegen, turning hard failures into recoveries.
 */

import { describe, it, expect } from "vitest";

// These imports will fail until we create the module
import {
  needsSampleInput,
  generateSyntheticSampleInput,
  healAgentTaskSpec,
  healSpec,
  coerceSpecTypes,
  inferMissingFields,
} from "../src/scenarios/spec-auto-heal.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import {
  parseAgentTaskSpec,
  SPEC_END,
  SPEC_START,
} from "../src/scenarios/agent-task-designer.js";
import {
  parseOperatorLoopSpec,
  OPERATOR_LOOP_SPEC_END,
  OPERATOR_LOOP_SPEC_START,
} from "../src/scenarios/operator-loop-designer.js";
import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";
import { buildAgentTaskSolveSpec } from "../src/knowledge/solver.js";

// ---------------------------------------------------------------------------
// Sample input detection (port of Python's needs_sample_input)
// ---------------------------------------------------------------------------

describe("needsSampleInput", () => {
  it("returns true when prompt says 'you will be provided with' and no sampleInput", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "You will be provided with a dataset. Analyze the trends.",
      judgeRubric: "Evaluate accuracy",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    expect(needsSampleInput(spec)).toBe(true);
  });

  it("returns true for 'given the following data' without inline data", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "Given the following data, summarize the key findings.",
      judgeRubric: "Evaluate completeness",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    expect(needsSampleInput(spec)).toBe(true);
  });

  it("returns false when sampleInput is already provided", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "You will be provided with a dataset.",
      judgeRubric: "Evaluate",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
      sampleInput: '{"data": [1, 2, 3]}',
    };
    expect(needsSampleInput(spec)).toBe(false);
  });

  it("returns false when prompt has inline data after reference", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: 'Analyze the following data:\n```json\n{"revenue": 100}\n```',
      judgeRubric: "Evaluate",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    expect(needsSampleInput(spec)).toBe(false);
  });

  it("returns false for prompts with no data references", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "Write a poem about clouds.",
      judgeRubric: "Evaluate creativity",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    expect(needsSampleInput(spec)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Synthetic sample input generation
// ---------------------------------------------------------------------------

describe("generateSyntheticSampleInput", () => {
  it("generates valid JSON from domain hints", () => {
    const sample = generateSyntheticSampleInput(
      "Analyze patient records and drug interactions",
      "Medical data analysis",
    );
    const parsed = JSON.parse(sample);
    expect(parsed).toBeDefined();
    expect(typeof parsed).toBe("object");
  });

  it("generates fallback structure when no domain hints found", () => {
    const sample = generateSyntheticSampleInput("Do it", "");
    const parsed = JSON.parse(sample);
    expect(parsed).toBeDefined();
    expect(parsed.input_data).toBeDefined();
  });

  it("includes domain-relevant field names", () => {
    const sample = generateSyntheticSampleInput(
      "Analyze customer records and transaction data",
      "Customer analysis",
    );
    const text = sample.toLowerCase();
    expect(text).toMatch(/customer|transaction|record|data/);
  });
});

// ---------------------------------------------------------------------------
// Full agent_task spec healing
// ---------------------------------------------------------------------------

describe("healAgentTaskSpec", () => {
  it("adds sampleInput when prompt references external data", () => {
    const spec: AgentTaskSpec = {
      taskPrompt:
        "You will be provided with patient records. Identify drug interactions.",
      judgeRubric: "Evaluate accuracy",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    const healed = healAgentTaskSpec(spec, "Medical analysis task");
    expect(healed.sampleInput).toBeDefined();
    expect(healed.sampleInput!.length).toBeGreaterThan(0);
    // Original prompt should be unchanged
    expect(healed.taskPrompt).toBe(spec.taskPrompt);
  });

  it("does not modify a spec that needs no healing", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "Write a poem about clouds.",
      judgeRubric: "Evaluate creativity",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    const healed = healAgentTaskSpec(spec);
    expect(healed).toEqual(spec);
  });
});

// ---------------------------------------------------------------------------
// Type coercion
// ---------------------------------------------------------------------------

describe("coerceSpecTypes", () => {
  it("coerces string numbers to actual numbers", () => {
    const spec = { maxSteps: "10", max_steps: "20", description: "test" };
    const fixed = coerceSpecTypes(spec);
    expect(fixed.maxSteps).toBe(10);
    expect(fixed.max_steps).toBe(20);
  });

  it("coerces string booleans", () => {
    const spec = { retryable: "true", enabled: "false" };
    const fixed = coerceSpecTypes(spec);
    expect(fixed.retryable).toBe(true);
    expect(fixed.enabled).toBe(false);
  });

  it("leaves correct types alone", () => {
    const spec = { maxSteps: 10, description: "test", items: [1, 2] };
    const fixed = coerceSpecTypes(spec);
    expect(fixed).toEqual(spec);
  });
});

// ---------------------------------------------------------------------------
// Missing field inference
// ---------------------------------------------------------------------------

describe("inferMissingFields", () => {
  it("infers description from taskPrompt when empty", () => {
    const spec = {
      taskPrompt: "Write a summary of quarterly earnings",
      description: "",
    };
    const fixed = inferMissingFields(spec);
    expect(fixed.description).toBeTruthy();
    expect(fixed.description.length).toBeGreaterThan(0);
  });

  it("infers rubric when missing", () => {
    const spec = {
      taskPrompt: "Analyze this code for bugs",
      rubric: "",
      judgeRubric: "",
    };
    const fixed = inferMissingFields(spec);
    expect(fixed.rubric || fixed.judgeRubric).toBeTruthy();
  });

  it("does not overwrite existing fields", () => {
    const spec = {
      taskPrompt: "Test",
      description: "My description",
      rubric: "My rubric",
    };
    const fixed = inferMissingFields(spec);
    expect(fixed.description).toBe("My description");
    expect(fixed.rubric).toBe("My rubric");
  });
});

// ---------------------------------------------------------------------------
// Generic spec healing (all families)
// ---------------------------------------------------------------------------

describe("healSpec", () => {
  it("applies type coercion + field inference in one pass", () => {
    const spec = {
      taskPrompt: "Write a code review for a pull request",
      description: "",
      maxSteps: "15",
      rubric: "",
    };

    const healed = healSpec(spec, "agent_task");
    expect(healed.maxSteps).toBe(15);
    expect(healed.description).toBeTruthy();
  });

  it("applies sampleInput healing for agent_task family", () => {
    const spec = {
      taskPrompt: "You will be provided with a dataset. Find anomalies.",
      judgeRubric: "Evaluate",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    const healed = healSpec(spec, "agent_task");
    expect(healed.sampleInput).toBeDefined();
  });

  it("heals snake_case agent_task specs before strict parsing", () => {
    const parsed = parseAgentTaskSpec(
      [
        SPEC_START,
        JSON.stringify(
          {
            task_prompt:
              "You will be provided with an outage log. Summarize the root cause.",
            judge_rubric: "Evaluate accuracy",
            output_format: "free_text",
            judge_model: "",
            max_rounds: "2",
            quality_threshold: "0.85",
          },
          null,
          2,
        ),
        SPEC_END,
      ].join("\n"),
    );

    expect(parsed.maxRounds).toBe(2);
    expect(parsed.qualityThreshold).toBe(0.85);
    expect(parsed.sampleInput).toBeDefined();
  });

  it("heals codegen-family numeric fields before designer parsing", () => {
    const parsed = parseOperatorLoopSpec(
      [
        OPERATOR_LOOP_SPEC_START,
        JSON.stringify(
          {
            description: "Escalate risky operator decisions",
            environment_description: "A live operations console",
            initial_state_description: "A pending incident queue",
            escalation_policy: {
              escalation_threshold: "high",
              max_escalations: "3",
            },
            success_criteria: ["Escalate when needed", "Resolve the incident"],
            failure_modes: ["Missed escalation"],
            max_steps: "10",
            actions: [
              {
                name: "inspect",
                description: "Inspect the queue",
                parameters: {},
                preconditions: [],
                effects: ["queue_reviewed"],
              },
              {
                name: "escalate",
                description: "Escalate to operator",
                parameters: {},
                preconditions: ["queue_reviewed"],
                effects: ["operator_engaged"],
              },
            ],
          },
          null,
          2,
        ),
        OPERATOR_LOOP_SPEC_END,
      ].join("\n"),
    );

    expect(parsed.maxSteps).toBe(10);
    expect(parsed.escalationPolicy.maxEscalations).toBe(3);
  });

  it("returns a healed agent_task spec from createScenarioFromDescription", async () => {
    const provider = {
      defaultModel: () => "test-model",
      complete: async () => ({
        text: JSON.stringify({
          family: "agent_task",
          name: "incident_summary",
          taskPrompt:
            "You will be provided with an incident report. Summarize the outage.",
          rubric: "Evaluate accuracy and completeness.",
          outputFormat: "free_text",
          maxRounds: "2",
          qualityThreshold: "0.88",
        }),
      }),
    };

    const result = await createScenarioFromDescription(
      "Summarize an incident report",
      provider,
    );

    expect(result.spec.sampleInput).toBeDefined();
    expect(result.spec.maxRounds).toBe(2);
    expect(result.spec.qualityThreshold).toBe(0.88);
  });

  it("builds solve-time agent_task specs without dropping healed fields", () => {
    const spec = buildAgentTaskSolveSpec(
      {
        taskPrompt:
          "You will be provided with customer transaction data. Find anomalies.",
        rubric: "Evaluate correctness",
        outputFormat: "free_text",
        maxRounds: "3",
        qualityThreshold: "0.92",
        sampleInput: '{"transactions":[{"id":"t1"}]}',
        referenceContext:
          "Fraud analysts compare amount, merchant, and timing.",
        contextPreparation:
          "Load the latest fraud rules before drafting the summary.",
        requiredContextKeys: ["referenceContext", "sampleInput"],
      },
      1,
    );

    expect(spec.maxRounds).toBe(3);
    expect(spec.qualityThreshold).toBe(0.92);
    expect(spec.sampleInput).toContain("transactions");
    expect(spec.referenceContext).toContain("Fraud analysts");
    expect(spec.contextPreparation).toContain("fraud rules");
    expect(spec.requiredContextKeys).toEqual([
      "referenceContext",
      "sampleInput",
    ]);
  });

  it("returns a copy, not a mutation", () => {
    const original = { taskPrompt: "Test", description: "", maxSteps: "5" };
    const healed = healSpec(original, "agent_task");
    expect(original.description).toBe("");
    expect(original.maxSteps).toBe("5");
    expect(healed.description).toBeTruthy();
    expect(healed.maxSteps).toBe(5);
  });
});

// ---------------------------------------------------------------------------
// Precondition normalization (AC-529)
// ---------------------------------------------------------------------------

describe("healSpec normalizes simulation preconditions (AC-529)", () => {
  it("strips prose preconditions that don't match any action name", () => {
    const spec = {
      actions: [
        {
          name: "deploy",
          description: "Deploy service",
          parameters: {},
          preconditions: ["The environment is ready."],
          effects: [],
        },
        {
          name: "test",
          description: "Run tests",
          parameters: {},
          preconditions: ["deploy"],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "simulation");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toContain("deploy");
    expect(actions[0].preconditions).not.toContain("The environment is ready.");
    expect(actions[0].preconditions).toHaveLength(0);
  });

  it("fuzzy-matches prose preconditions to closest action name", () => {
    const spec = {
      actions: [
        {
          name: "provision_infrastructure",
          description: "Provision infra",
          parameters: {},
          preconditions: [],
          effects: [],
        },
        {
          name: "deploy",
          description: "Deploy",
          parameters: {},
          preconditions: ["provision infrastructure"],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "simulation");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toContain("provision_infrastructure");
  });

  it("preserves hyphenated action names when preconditions use spaces", () => {
    const spec = {
      actions: [
        {
          name: "run-tests",
          description: "Run tests",
          parameters: {},
          preconditions: [],
          effects: [],
        },
        {
          name: "deploy",
          description: "Deploy",
          parameters: {},
          preconditions: ["run tests"],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "simulation");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toEqual(["run-tests"]);
  });

  it("preserves dotted action names when preconditions use spaces", () => {
    const spec = {
      actions: [
        {
          name: "provision.infrastructure",
          description: "Provision infra",
          parameters: {},
          preconditions: [],
          effects: [],
        },
        {
          name: "deploy",
          description: "Deploy",
          parameters: {},
          preconditions: ["provision infrastructure"],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "simulation");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toEqual(["provision.infrastructure"]);
  });

  it("preserves valid action-name preconditions unchanged", () => {
    const spec = {
      actions: [
        {
          name: "setup",
          description: "Setup",
          parameters: {},
          preconditions: [],
          effects: [],
        },
        {
          name: "deploy",
          description: "Deploy",
          parameters: {},
          preconditions: ["setup"],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "simulation");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toEqual(["setup"]);
  });

  it("applies to all simulation-like families", () => {
    for (const family of [
      "simulation",
      "workflow",
      "operator_loop",
      "coordination",
      "investigation",
    ]) {
      const spec = {
        actions: [
          {
            name: "act",
            description: "d",
            parameters: {},
            preconditions: ["A hostile event occurred."],
            effects: [],
          },
        ],
      };
      const healed = healSpec(spec, family);
      const actions = healed.actions as Array<{ preconditions: string[] }>;
      expect(actions[0].preconditions).toHaveLength(0);
    }
  });

  it("does not apply precondition healing to agent_task family", () => {
    const spec = {
      taskPrompt: "test",
      judgeRubric: "test",
      actions: [
        {
          name: "act",
          description: "d",
          parameters: {},
          preconditions: ["Something prose-like."],
          effects: [],
        },
      ],
    };
    const healed = healSpec(spec, "agent_task");
    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[0].preconditions).toContain("Something prose-like.");
  });
});
