import { describe, it, expect } from "vitest";
import { mkdtempSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { AgentTaskSpecSchema, parseRawSpec } from "../src/scenarios/agent-task-spec.js";
import { parseAgentTaskSpec, SPEC_START, SPEC_END } from "../src/scenarios/agent-task-designer.js";
import {
  ARTIFACT_SPEC_END,
  ARTIFACT_SPEC_START,
} from "../src/scenarios/artifact-editing-designer.js";
import {
  COORDINATION_SPEC_END,
  COORDINATION_SPEC_START,
} from "../src/scenarios/coordination-designer.js";
import {
  INVESTIGATION_SPEC_END,
  INVESTIGATION_SPEC_START,
} from "../src/scenarios/investigation-designer.js";
import {
  NEGOTIATION_SPEC_END,
  NEGOTIATION_SPEC_START,
} from "../src/scenarios/negotiation-designer.js";
import {
  OPERATOR_LOOP_SPEC_END,
  OPERATOR_LOOP_SPEC_START,
} from "../src/scenarios/operator-loop-designer.js";
import {
  SCHEMA_EVOLUTION_SPEC_END,
  SCHEMA_EVOLUTION_SPEC_START,
} from "../src/scenarios/schema-evolution-designer.js";
import { SIM_SPEC_END, SIM_SPEC_START } from "../src/scenarios/simulation-designer.js";
import {
  TOOL_FRAGILITY_SPEC_END,
  TOOL_FRAGILITY_SPEC_START,
} from "../src/scenarios/tool-fragility-designer.js";
import { WORKFLOW_SPEC_END, WORKFLOW_SPEC_START } from "../src/scenarios/workflow-designer.js";
import { classifyScenarioFamily } from "../src/scenarios/family-classifier.js";
import { UnsupportedFamilyError, validateForFamily } from "../src/scenarios/family-pipeline.js";
import { getScenarioTypeMarker } from "../src/scenarios/families.js";
import { validateIntent, validateSpec } from "../src/scenarios/agent-task-validator.js";
import { createAgentTask } from "../src/scenarios/agent-task-factory.js";
import { AgentTaskCreator } from "../src/scenarios/agent-task-creator.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import type { CoordinationSpec } from "../src/scenarios/coordination-spec.js";
import type { InvestigationSpec } from "../src/scenarios/investigation-spec.js";
import type { NegotiationSpec } from "../src/scenarios/negotiation-spec.js";
import type { OperatorLoopSpec } from "../src/scenarios/operator-loop-spec.js";
import type { SchemaEvolutionSpec } from "../src/scenarios/schema-evolution-spec.js";
import type { SimulationSpec } from "../src/scenarios/simulation-spec.js";
import type { ToolFragilitySpec } from "../src/scenarios/tool-fragility-spec.js";
import type { WorkflowSpec } from "../src/scenarios/workflow-spec.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";
import { AgentTaskResultSchema } from "../src/types/index.js";

// --- Helpers ---

const SAMPLE_SPEC: AgentTaskSpec = {
  taskPrompt: "Write a haiku about testing software.",
  judgeRubric:
    "Evaluate on: (1) Format — valid haiku (5-7-5)? (2) Relevance — about testing? (3) Creativity",
  outputFormat: "free_text",
  judgeModel: "claude-sonnet-4-20250514",
  maxRounds: 1,
  qualityThreshold: 0.9,
};

function mockLlmResponse(spec: AgentTaskSpec): string {
  const data: Record<string, unknown> = {
    task_prompt: spec.taskPrompt,
    judge_rubric: spec.judgeRubric,
    output_format: spec.outputFormat,
    judge_model: spec.judgeModel,
  };
  return `Here is the spec:\n${SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SPEC_END}\n`;
}

function mockSimulationResponse(): string {
  const data = {
    description: "Recover a multi-step API workflow.",
    environment_description: "Mock API orchestration environment.",
    initial_state_description: "No calls completed.",
    success_criteria: ["all required actions complete", "invalid order is recovered"],
    failure_modes: ["dependency mismatch", "partial side effects"],
    max_steps: 6,
    actions: [
      {
        name: "book_flight",
        description: "Reserve a flight.",
        parameters: { flight_id: "string" },
        preconditions: [],
        effects: ["flight_reserved"],
      },
      {
        name: "book_hotel",
        description: "Reserve a hotel.",
        parameters: { hotel_id: "string" },
        preconditions: ["book_flight"],
        effects: ["hotel_reserved"],
      },
    ],
  };
  return `${SIM_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SIM_SPEC_END}\n`;
}

function mockArtifactEditingResponse(): string {
  const data = {
    task_description: "Update a YAML config to add a database section.",
    rubric: "Evaluate artifact correctness, validator success, and minimal unnecessary changes.",
    validation_rules: [
      'config/app.yaml must contain "database:"',
      'config/app.yaml must contain "host:"',
      'config/app.yaml must contain "port:"',
    ],
    artifacts: [
      {
        path: "config/app.yaml",
        content: "app:\n  name: myapp\n  port: 8080\n",
        content_type: "yaml",
      },
    ],
  };
  return `${ARTIFACT_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${ARTIFACT_SPEC_END}\n`;
}

function mockInvestigationResponse(): string {
  const data = {
    description:
      "Investigate a production outage by gathering evidence and identifying the root cause.",
    environment_description: "Mock service environment with logs and dashboards.",
    initial_state_description: "An outage is active and only partial evidence is visible.",
    evidence_pool_description:
      "Logs implicate the auth service, metrics show latency spikes, and a cron-job entry is a red herring.",
    diagnosis_target: "A bad auth deployment exhausted the database connection pool.",
    success_criteria: [
      "collect enough evidence to explain the outage",
      "identify the correct diagnosis without relying on red herrings",
    ],
    failure_modes: ["following a cron-job red herring"],
    max_steps: 6,
    actions: [
      {
        name: "inspect_logs",
        description: "Review service logs around the incident.",
        parameters: { service: "string" },
        preconditions: [],
        effects: ["log_evidence_collected"],
      },
      {
        name: "query_metrics",
        description: "Check dashboard metrics related to the outage.",
        parameters: { metric: "string" },
        preconditions: [],
        effects: ["metrics_evidence_collected"],
      },
      {
        name: "record_diagnosis",
        description: "Submit the final diagnosis.",
        parameters: { diagnosis: "string" },
        preconditions: ["inspect_logs", "query_metrics"],
        effects: ["diagnosis_recorded"],
      },
    ],
  };
  return `${INVESTIGATION_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${INVESTIGATION_SPEC_END}\n`;
}

function mockWorkflowResponse(): string {
  const data = {
    description:
      "Execute an order-processing workflow with compensation when downstream steps fail.",
    environment_description:
      "Mock commerce workflow with payment, inventory, and notification side effects.",
    initial_state_description: "No workflow steps have run yet.",
    workflow_steps: [
      {
        name: "charge_payment",
        description: "Charge the payment method.",
        idempotent: false,
        reversible: true,
        compensation: "refund_payment",
      },
      {
        name: "reserve_inventory",
        description: "Reserve inventory for the order.",
        idempotent: true,
        reversible: true,
        compensation: "release_inventory",
      },
      {
        name: "send_confirmation",
        description: "Send the confirmation notification.",
        idempotent: true,
        reversible: false,
      },
    ],
    success_criteria: [
      "all required workflow steps complete in order",
      "reversible side effects are compensated if failures occur",
    ],
    failure_modes: ["payment failure", "notification sent before rollback"],
    max_steps: 7,
    actions: [
      {
        name: "charge_payment",
        description: "Charge the payment method.",
        parameters: { payment_id: "string" },
        preconditions: [],
        effects: ["payment_captured"],
      },
      {
        name: "reserve_inventory",
        description: "Reserve inventory for the order.",
        parameters: { sku: "string" },
        preconditions: ["charge_payment"],
        effects: ["inventory_reserved"],
      },
      {
        name: "send_confirmation",
        description: "Send the confirmation notification.",
        parameters: { channel: "string" },
        preconditions: ["reserve_inventory"],
        effects: ["confirmation_sent"],
      },
    ],
  };
  return `${WORKFLOW_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${WORKFLOW_SPEC_END}\n`;
}

function mockSchemaEvolutionResponse(): string {
  const data = {
    description: "Adapt to schema changes during a data migration.",
    environment_description: "Versioned API environment with evolving fields.",
    initial_state_description: "Version 1 schema is currently active.",
    mutations: [
      {
        version: 2,
        description: "Add a priority field.",
        breaking: false,
        fields_added: ["priority"],
        fields_removed: [],
        fields_modified: {},
      },
      {
        version: 3,
        description: "Rename status to state and remove legacy_id.",
        breaking: true,
        fields_added: ["state"],
        fields_removed: ["status", "legacy_id"],
        fields_modified: {},
      },
    ],
    success_criteria: ["detect schema changes", "discard stale assumptions"],
    failure_modes: ["using removed fields"],
    max_steps: 8,
    actions: [
      {
        name: "query_api",
        description: "Query the current schema.",
        parameters: { endpoint: "string" },
        preconditions: [],
        effects: ["schema_observed"],
      },
      {
        name: "validate_schema",
        description: "Validate assumptions against the schema.",
        parameters: {},
        preconditions: ["query_api"],
        effects: ["schema_validated"],
      },
    ],
  };
  return `${SCHEMA_EVOLUTION_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SCHEMA_EVOLUTION_SPEC_END}\n`;
}

function mockToolFragilityResponse(): string {
  const data = {
    description: "Adapt to tool contract drift in a pipeline.",
    environment_description: "Versioned services with unstable response formats.",
    initial_state_description: "All tools initially operate at stable version 1.",
    tool_contracts: [
      {
        tool_name: "search_api",
        version: 1,
        description: "Search endpoint returning a flat list.",
      },
      { tool_name: "transform_api", version: 1, description: "Data transform endpoint." },
    ],
    success_criteria: ["detect tool drift", "adapt without wasted attempts"],
    failure_modes: ["using stale response format"],
    max_steps: 8,
    actions: [
      {
        name: "call_search",
        description: "Call the search API.",
        parameters: { query: "string" },
        preconditions: [],
        effects: ["search_results_obtained"],
      },
      {
        name: "call_transform",
        description: "Call the transform API.",
        parameters: { data: "string" },
        preconditions: ["call_search"],
        effects: ["data_transformed"],
      },
    ],
  };
  return `${TOOL_FRAGILITY_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${TOOL_FRAGILITY_SPEC_END}\n`;
}

function mockNegotiationResponse(): string {
  const data = {
    description: "Negotiate a contract with hidden opponent preferences and BATNA constraints.",
    environment_description: "Buyer-seller negotiation over price, timing, and warranty.",
    initial_state_description: "Both sides have opening positions and hidden priorities.",
    hidden_preferences: {
      priorities: { price: 0.6, delivery_time: 0.3, warranty: 0.1 },
      reservation_value: 50.0,
      aspiration_value: 85.0,
      batna_description: "Switch to a slower alternative vendor.",
    },
    max_rounds: 5,
    success_criteria: ["reach agreement above reservation value", "model opponent priorities"],
    failure_modes: ["deadlock without agreement"],
    actions: [
      {
        name: "make_offer",
        description: "Propose contract terms.",
        parameters: { terms: "dict" },
        preconditions: [],
        effects: ["offer_on_table"],
      },
      {
        name: "counter_offer",
        description: "Respond with modified terms.",
        parameters: { terms: "dict" },
        preconditions: ["make_offer"],
        effects: ["counter_on_table"],
      },
      {
        name: "accept",
        description: "Accept the current offer.",
        parameters: {},
        preconditions: ["make_offer"],
        effects: ["deal_closed"],
      },
    ],
  };
  return `${NEGOTIATION_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${NEGOTIATION_SPEC_END}\n`;
}

function mockOperatorLoopResponse(): string {
  const data = {
    description: "Customer support triage with escalation policy.",
    environment_description: "Help desk system with tiered support.",
    initial_state_description: "Ticket received, agent begins triage.",
    escalation_policy: {
      escalation_threshold: "high",
      max_escalations: 3,
    },
    success_criteria: ["resolve issue or correctly escalate", "minimize unnecessary escalations"],
    failure_modes: ["over-escalation", "under-escalation"],
    max_steps: 10,
    actions: [
      {
        name: "respond",
        description: "Reply to the customer directly.",
        parameters: { message: "string" },
        preconditions: [],
        effects: ["response_sent"],
      },
      {
        name: "escalate_ticket",
        description: "Escalate to a human operator.",
        parameters: { reason: "string" },
        preconditions: [],
        effects: ["escalated"],
      },
    ],
  };
  return `${OPERATOR_LOOP_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${OPERATOR_LOOP_SPEC_END}\n`;
}

function mockCoordinationResponse(): string {
  const data = {
    description: "Multi-agent research report writing.",
    environment_description: "Research team with partial information.",
    initial_state_description: "Task partitioned across workers.",
    workers: [
      { worker_id: "researcher", role: "data gatherer" },
      { worker_id: "writer", role: "report writer" },
    ],
    success_criteria: ["coherent merged report", "minimal duplication across sections"],
    failure_modes: ["duplicate content across workers", "lost information during handoff"],
    max_steps: 10,
    actions: [
      {
        name: "research",
        description: "Gather data on assigned topic.",
        parameters: { topic: "string" },
        preconditions: [],
        effects: ["data_gathered"],
      },
      {
        name: "write_section",
        description: "Write a report section.",
        parameters: { section: "string" },
        preconditions: ["research"],
        effects: ["section_written"],
      },
    ],
  };
  return `${COORDINATION_SPEC_START}\n${JSON.stringify(data, null, 2)}\n${COORDINATION_SPEC_END}\n`;
}

function makeMockProvider(response = "mock output"): LLMProvider {
  return {
    complete: async () =>
      ({
        text: response,
        model: "mock",
        usage: { inputTokens: 0, outputTokens: 0 },
      }) as CompletionResult,
    defaultModel: () => "mock-model",
  };
}

// --- Tests ---

describe("AgentTaskSpec", () => {
  it("parses valid spec", () => {
    const spec = parseRawSpec({
      task_prompt: "Do something",
      judge_rubric: "Check quality",
    });
    expect(spec.taskPrompt).toBe("Do something");
    expect(spec.outputFormat).toBe("free_text");
    expect(spec.maxRounds).toBe(1);
    expect(spec.qualityThreshold).toBe(0.9);
  });

  it("rejects empty task_prompt", () => {
    expect(() => parseRawSpec({ task_prompt: "", judge_rubric: "ok" })).toThrow();
  });

  it("rejects invalid output_format", () => {
    expect(() =>
      AgentTaskSpecSchema.parse({
        taskPrompt: "Do something",
        judgeRubric: "Check",
        outputFormat: "invalid",
      }),
    ).toThrow();
  });

  it("accepts optional fields", () => {
    const spec = parseRawSpec({
      task_prompt: "Write about RLMs",
      judge_rubric: "Check accuracy",
      reference_context: "RLM = Recursive Language Model",
      required_concepts: ["context folding"],
      max_rounds: 3,
      quality_threshold: 0.8,
    });
    expect(spec.referenceContext).toBe("RLM = Recursive Language Model");
    expect(spec.requiredConcepts).toEqual(["context folding"]);
    expect(spec.maxRounds).toBe(3);
    expect(spec.qualityThreshold).toBe(0.8);
  });

  it("parses sample_input field", () => {
    const spec = parseRawSpec({
      task_prompt: "Analyze this outage report",
      judge_rubric: "Check completeness",
      sample_input: "Service X went down at 3am due to a memory leak in the cache layer.",
    });
    expect(spec.sampleInput).toBe(
      "Service X went down at 3am due to a memory leak in the cache layer.",
    );
  });

  it("sample_input defaults to null when not provided", () => {
    const spec = parseRawSpec({
      task_prompt: "Do something",
      judge_rubric: "Check quality",
    });
    expect(spec.sampleInput).toBeNull();
  });
});

describe("Designer", () => {
  it("parses spec from LLM response with delimiters", () => {
    const response = mockLlmResponse(SAMPLE_SPEC);
    const spec = parseAgentTaskSpec(response);
    expect(spec.taskPrompt).toBe(SAMPLE_SPEC.taskPrompt);
    expect(spec.judgeRubric).toBe(SAMPLE_SPEC.judgeRubric);
  });

  it("throws on missing delimiters", () => {
    expect(() => parseAgentTaskSpec("no delimiters here")).toThrow("does not contain");
  });

  it("handles extra text around delimiters", () => {
    const response = `Some preamble text.\n${mockLlmResponse(SAMPLE_SPEC)}\nSome postscript.`;
    const spec = parseAgentTaskSpec(response);
    expect(spec.taskPrompt).toBe(SAMPLE_SPEC.taskPrompt);
  });

  it("falls back to raw JSON when delimiters are missing", () => {
    const spec = parseAgentTaskSpec(JSON.stringify({
      task_prompt: SAMPLE_SPEC.taskPrompt,
      judge_rubric: SAMPLE_SPEC.judgeRubric,
      output_format: SAMPLE_SPEC.outputFormat,
      judge_model: SAMPLE_SPEC.judgeModel,
    }));
    expect(spec.taskPrompt).toBe(SAMPLE_SPEC.taskPrompt);
  });
});

describe("Validator", () => {
  it("validates correct spec", () => {
    expect(validateSpec(SAMPLE_SPEC)).toEqual([]);
  });

  it("catches empty rubric", () => {
    const errors = validateSpec({ ...SAMPLE_SPEC, judgeRubric: "" });
    expect(errors.length).toBeGreaterThan(0);
    expect(errors.some((e) => e.includes("judge_rubric") || e.includes("judgeRubric"))).toBe(true);
  });

  it("flags free_text when the description explicitly requests JSON", () => {
    const errors = validateIntent(
      "Produce a machine-readable JSON response with fields title and score",
      {
        ...SAMPLE_SPEC,
        taskPrompt: "Write a short summary of the result and mention the score.",
        judgeRubric: "Score clarity and coverage.",
        outputFormat: "free_text",
      },
    );
    expect(errors.some((e) => e.includes("structured JSON output"))).toBe(true);
  });
});

describe("FamilyPipeline", () => {
  it("validates agent_task specs through the family pipeline", () => {
    expect(validateForFamily("agent_task", SAMPLE_SPEC)).toEqual([]);
  });

  it("validates simulation specs through the family pipeline", () => {
    const spec: SimulationSpec = {
      description: "Recover a multi-step API workflow.",
      environmentDescription: "Mock API orchestration environment.",
      initialStateDescription: "No calls completed.",
      successCriteria: ["all required actions complete", "invalid order is recovered"],
      failureModes: ["dependency mismatch", "partial side effects"],
      maxSteps: 6,
      actions: [
        {
          name: "book_flight",
          description: "Reserve a flight.",
          parameters: { flight_id: "string" },
          preconditions: [],
          effects: ["flight_reserved"],
        },
        {
          name: "book_hotel",
          description: "Reserve a hotel.",
          parameters: { hotel_id: "string" },
          preconditions: ["book_flight"],
          effects: ["hotel_reserved"],
        },
      ],
    };
    expect(validateForFamily("simulation", spec)).toEqual([]);
  });

  it("validates artifact-editing specs through the family pipeline", () => {
    const spec = {
      taskDescription: "Update a YAML config to add a database section.",
      rubric: "Evaluate artifact correctness, validator success, and minimal unnecessary changes.",
      validationRules: [
        'config/app.yaml must contain "database:"',
        'config/app.yaml must contain "host:"',
      ],
      artifacts: [
        {
          path: "config/app.yaml",
          content: "app:\n  name: myapp\n  port: 8080\n",
          contentType: "yaml",
          metadata: {},
        },
      ],
    };
    expect(validateForFamily("artifact_editing", spec)).toEqual([]);
  });

  it("validates investigation specs through the family pipeline", () => {
    const spec: InvestigationSpec = {
      description: "Investigate a production outage.",
      environmentDescription: "Mock service environment with logs.",
      initialStateDescription: "The outage is ongoing.",
      evidencePoolDescription: "Logs implicate auth; a cron job is a red herring.",
      diagnosisTarget: "A bad auth deployment exhausted the DB pool.",
      successCriteria: ["collect evidence", "identify the correct diagnosis"],
      failureModes: ["following the red herring"],
      maxSteps: 6,
      actions: [
        {
          name: "inspect_logs",
          description: "Inspect logs",
          parameters: { service: "string" },
          preconditions: [],
          effects: ["log_evidence"],
        },
        {
          name: "record_diagnosis",
          description: "Record diagnosis",
          parameters: { diagnosis: "string" },
          preconditions: ["inspect_logs"],
          effects: ["diagnosis_recorded"],
        },
      ],
    };
    expect(validateForFamily("investigation", spec)).toEqual([]);
  });

  it("validates workflow specs through the family pipeline", () => {
    const spec: WorkflowSpec = {
      description: "Execute an order-processing workflow.",
      environmentDescription: "Mock commerce workflow.",
      initialStateDescription: "Nothing has run yet.",
      workflowSteps: [
        {
          name: "charge_payment",
          description: "Charge payment",
          idempotent: false,
          reversible: true,
          compensation: "refund_payment",
        },
        {
          name: "reserve_inventory",
          description: "Reserve inventory",
          idempotent: true,
          reversible: true,
          compensation: "release_inventory",
        },
      ],
      successCriteria: ["steps complete in order", "compensation contains side effects"],
      failureModes: ["payment failure"],
      maxSteps: 6,
      actions: [
        {
          name: "charge_payment",
          description: "Charge payment",
          parameters: { payment_id: "string" },
          preconditions: [],
          effects: ["payment_captured"],
        },
        {
          name: "reserve_inventory",
          description: "Reserve inventory",
          parameters: { sku: "string" },
          preconditions: ["charge_payment"],
          effects: ["inventory_reserved"],
        },
      ],
    };
    expect(validateForFamily("workflow", spec)).toEqual([]);
  });

  it("validates schema-evolution specs through the family pipeline", () => {
    const spec: SchemaEvolutionSpec = {
      description: "Adapt to schema evolution.",
      environmentDescription: "Versioned API environment.",
      initialStateDescription: "Schema v1 is active.",
      mutations: [
        {
          version: 2,
          description: "Add priority.",
          breaking: false,
          fieldsAdded: ["priority"],
          fieldsRemoved: [],
          fieldsModified: {},
        },
        {
          version: 3,
          description: "Rename status.",
          breaking: true,
          fieldsAdded: ["state"],
          fieldsRemoved: ["status"],
          fieldsModified: {},
        },
      ],
      successCriteria: ["detect changes"],
      failureModes: ["using removed fields"],
      maxSteps: 8,
      actions: [
        {
          name: "query_api",
          description: "Query schema",
          parameters: { endpoint: "string" },
          preconditions: [],
          effects: ["schema_observed"],
        },
        {
          name: "validate_schema",
          description: "Validate schema",
          parameters: {},
          preconditions: ["query_api"],
          effects: ["schema_validated"],
        },
      ],
    };
    expect(validateForFamily("schema_evolution", spec)).toEqual([]);
  });

  it("validates tool-fragility specs through the family pipeline", () => {
    const spec: ToolFragilitySpec = {
      description: "Adapt to tool drift.",
      environmentDescription: "Versioned service environment.",
      initialStateDescription: "Tools are on v1.",
      toolContracts: [
        { toolName: "search_api", version: 1, description: "Search API" },
        { toolName: "transform_api", version: 1, description: "Transform API" },
      ],
      successCriteria: ["detect drift"],
      failureModes: ["using stale responses"],
      maxSteps: 8,
      actions: [
        {
          name: "call_search",
          description: "Call search",
          parameters: { query: "string" },
          preconditions: [],
          effects: ["search_results"],
        },
        {
          name: "call_transform",
          description: "Call transform",
          parameters: { data: "string" },
          preconditions: ["call_search"],
          effects: ["transform_complete"],
        },
      ],
    };
    expect(validateForFamily("tool_fragility", spec)).toEqual([]);
  });

  it("validates negotiation specs through the family pipeline", () => {
    const spec: NegotiationSpec = {
      description: "Negotiate a contract with hidden BATNA.",
      environmentDescription: "Buyer-seller contract negotiation.",
      initialStateDescription: "Both parties have opening positions.",
      hiddenPreferences: {
        priorities: { price: 0.6, delivery_time: 0.3, warranty: 0.1 },
        reservationValue: 50.0,
        aspirationValue: 85.0,
        batnaDescription: "Switch to a slower alternative vendor.",
      },
      maxRounds: 5,
      successCriteria: ["reach agreement", "model opponent priorities"],
      failureModes: ["deadlock"],
      actions: [
        {
          name: "make_offer",
          description: "Make an offer",
          parameters: { terms: "dict" },
          preconditions: [],
          effects: ["offer_on_table"],
        },
        {
          name: "accept",
          description: "Accept an offer",
          parameters: {},
          preconditions: ["make_offer"],
          effects: ["deal_closed"],
        },
      ],
      maxSteps: 10,
    };
    expect(validateForFamily("negotiation", spec)).toEqual([]);
  });

  it("validates operator-loop specs through the family pipeline", () => {
    const spec: OperatorLoopSpec = {
      description: "Support triage with escalation judgment.",
      environmentDescription: "Help desk system.",
      initialStateDescription: "A new ticket has arrived.",
      escalationPolicy: {
        escalationThreshold: "high",
        maxEscalations: 3,
      },
      successCriteria: ["resolve or correctly escalate"],
      failureModes: ["over-escalation", "under-escalation"],
      actions: [
        {
          name: "respond",
          description: "Reply to the customer",
          parameters: { message: "string" },
          preconditions: [],
          effects: ["response_sent"],
        },
        {
          name: "escalate_ticket",
          description: "Escalate to a human",
          parameters: { reason: "string" },
          preconditions: [],
          effects: ["escalated"],
        },
      ],
      maxSteps: 10,
    };
    expect(validateForFamily("operator_loop", spec)).toEqual([]);
  });

  it("validates coordination specs through the family pipeline", () => {
    const spec: CoordinationSpec = {
      description: "Coordinate workers on a shared task.",
      environmentDescription: "Research team with partial context.",
      initialStateDescription: "Task is partitioned.",
      workers: [
        { workerId: "researcher", role: "data gatherer" },
        { workerId: "writer", role: "report writer" },
      ],
      successCriteria: ["coherent merged output"],
      failureModes: ["duplicate work"],
      actions: [
        {
          name: "research",
          description: "Gather data",
          parameters: { topic: "string" },
          preconditions: [],
          effects: ["data_gathered"],
        },
        {
          name: "write_section",
          description: "Write section",
          parameters: { section: "string" },
          preconditions: ["research"],
          effects: ["section_written"],
        },
      ],
      maxSteps: 10,
    };
    expect(validateForFamily("coordination", spec)).toEqual([]);
  });

  it("rejects unsupported families instead of collapsing silently", () => {
    expect(() => validateForFamily("game", SAMPLE_SPEC)).toThrow(UnsupportedFamilyError);
  });
});

describe("Factory", () => {
  it("creates task with correct properties", () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "haiku_task" });
    expect(task.name).toBe("haiku_task");
    expect(task.getTaskPrompt({})).toBe(SAMPLE_SPEC.taskPrompt);
    expect(task.getRubric()).toBe(SAMPLE_SPEC.judgeRubric);
    expect(task.describeTask()).toBe(SAMPLE_SPEC.taskPrompt);
  });

  it("initialState includes name and format", () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "test" });
    const state = task.initialState();
    expect(state.taskName).toBe("test");
    expect(state.outputFormat).toBe("free_text");
  });

  it("prepareContext adds spec fields", async () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      referenceContext: "domain knowledge",
      contextPreparation: "load docs",
      referenceSources: ["https://example.com"],
    };
    const task = createAgentTask({ spec, name: "ctx_test" });
    const state = await task.prepareContext!({});
    expect(state.referenceContext).toBe("domain knowledge");
    expect(state.contextPreparation).toBe("load docs");
    expect(state.referenceSources).toEqual(["https://example.com"]);
  });

  it("validateContext catches missing keys", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      requiredContextKeys: ["source_data", "config"],
    };
    const task = createAgentTask({ spec, name: "val_test" });
    const errors = task.validateContext!({});
    expect(errors).toHaveLength(2);
    expect(errors[0]).toContain("source_data");
  });

  it("validateContext passes with all keys present", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      requiredContextKeys: ["source_data"],
    };
    const task = createAgentTask({ spec, name: "val_test" });
    const errors = task.validateContext!({ source_data: "present" });
    expect(errors).toHaveLength(0);
  });

  it("evaluateOutput throws without provider", async () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "no_provider" });
    await expect(task.evaluateOutput("output", {})).rejects.toThrow("provider required");
  });
});

describe("AgentTaskCreator", () => {
  it("uses real #private fields for creator internals", () => {
    const source = readFileSync(
      join(import.meta.dirname, "..", "src", "scenarios", "agent-task-creator.ts"),
      "utf-8",
    );

    expect(source).toContain("#provider");
    expect(source).toContain("#model");
    expect(source).toContain("#knowledgeRoot");
    expect(source).not.toContain("private provider:");
    expect(source).not.toContain("private model:");
    expect(source).not.toContain("private knowledgeRoot:");
  });

  it("derives name from description — uses the improved domain-preserving heuristic", () => {
    const provider = makeMockProvider(mockLlmResponse(SAMPLE_SPEC));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: "/tmp/unused",
    });
    const name = creator.deriveName("Write a haiku about testing software");
    expect(name.split("_")).toEqual(
      expect.arrayContaining(
        ["haiku", "testing", "software"].filter((word) => name.includes(word)),
      ),
    );
    // Single meaningful word
    expect(creator.deriveName("Create something")).toBe("something");
  });

  it("deriveName filters common stop words", () => {
    const provider = makeMockProvider(mockLlmResponse(SAMPLE_SPEC));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: "/tmp/unused",
    });
    // "I want an agent that writes incident postmortems" -> should contain "incident"
    const name1 = creator.deriveName(
      "I want an agent that can write clear, well-structured incident postmortems for production outages",
    );
    expect(name1).toContain("incident");
    expect(name1).not.toContain("want");
    expect(name1).not.toContain("agent");

    // "Create a tool that generates API documentation from code" -> should contain "documentation" or "api"
    const name2 = creator.deriveName("Create a tool that generates API documentation from code");
    expect(name2).toContain("documentation");

    // Simple case
    expect(creator.deriveName("haiku writer")).toBe("haiku_writer");

    // Empty string
    expect(creator.deriveName("")).toBe("custom");

    // All stop words
    expect(creator.deriveName("a the and")).toBe("custom");
  });

  it("deriveName deduplicates words", () => {
    const provider = makeMockProvider(mockLlmResponse(SAMPLE_SPEC));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: "/tmp/unused",
    });
    const name = creator.deriveName("test test test testing");
    // "test" appears 3 times but should only appear once; "testing" is longer
    expect(name).toBe("test_testing");
  });

  it("end-to-end: creates task and saves files", async () => {
    const response = mockLlmResponse(SAMPLE_SPEC);
    const provider = makeMockProvider(response);

    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-"));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: tmpDir,
    });

    const task = await creator.create("Write a haiku about testing software");
    expect(task.getTaskPrompt({})).toBe(SAMPLE_SPEC.taskPrompt);
    expect(task.getRubric()).toBe(SAMPLE_SPEC.judgeRubric);

    // Check files were saved
    const name = creator.deriveName("Write a haiku about testing software");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("agent_task"),
    );

    const specData = JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"));
    expect(specData.task_prompt).toBe(SAMPLE_SPEC.taskPrompt);
    expect(specData.judge_rubric).toBe(SAMPLE_SPEC.judgeRubric);
  });

  it("end-to-end with reference context", async () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      taskPrompt: "Write about RLMs",
      judgeRubric: "Check accuracy",
      referenceContext: "RLM = Recursive Language Model",
      referenceSources: ["https://example.com/rlm"],
      requiredConcepts: ["context folding"],
    };
    const response = mockLlmResponse(spec);
    // Need to build a response that includes the extra fields
    const data: Record<string, unknown> = {
      task_prompt: spec.taskPrompt,
      judge_rubric: spec.judgeRubric,
      output_format: spec.outputFormat,
      judge_model: spec.judgeModel,
      reference_context: spec.referenceContext,
      reference_sources: spec.referenceSources,
      required_concepts: spec.requiredConcepts,
    };
    const fullResponse = `${SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SPEC_END}`;
    const provider = makeMockProvider(fullResponse);

    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-ref-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });
    await creator.create("Write about recursive language models");

    const name = creator.deriveName("Write about recursive language models");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    const specData = JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"));
    expect(specData.reference_context).toBe("RLM = Recursive Language Model");
    expect(specData.reference_sources).toEqual(["https://example.com/rlm"]);
    expect(specData.required_concepts).toEqual(["context folding"]);
  });

  it("rejects drifted specs before task creation", async () => {
    const driftedSpec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      taskPrompt: "Write a detailed recipe for chocolate cake.",
      judgeRubric: "Evaluate recipe completeness and presentation.",
    };
    const provider = makeMockProvider(mockLlmResponse(driftedSpec));
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-drift-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    await expect(
      creator.create("Write a concise abstract summarizing a research paper"),
    ).rejects.toThrow("intent validation failed");
  });

  it("routes simulation-like descriptions into a simulation scenario scaffold", async () => {
    const provider = makeMockProvider(mockSimulationResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-sim-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Build a stateful API orchestration workflow with rollback",
    );
    expect("family" in scenario && scenario.family).toBe("simulation");

    const name = creator.deriveName("Build a stateful API orchestration workflow with rollback");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("simulation"),
    );
  });

  it("routes artifact-editing descriptions into an artifact-editing scaffold", async () => {
    const provider = makeMockProvider(mockArtifactEditingResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-artifact-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create("Edit a YAML config file to add a database section");
    expect("family" in scenario && scenario.family).toBe("artifact_editing");

    const name = creator.deriveName("Edit a YAML config file to add a database section");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("artifact_editing"),
    );
  });

  it("routes investigation descriptions into an investigation scaffold", async () => {
    const provider = makeMockProvider(mockInvestigationResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-investigation-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create an investigation where the agent gathers evidence, avoids red herrings, and finds the root cause",
    );
    expect("family" in scenario && scenario.family).toBe("investigation");

    const name = creator.deriveName(
      "Create an investigation where the agent gathers evidence, avoids red herrings, and finds the root cause",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("investigation"),
    );
  });

  it("routes workflow descriptions into a workflow scaffold", async () => {
    const provider = makeMockProvider(mockWorkflowResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-workflow-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create a transactional workflow with compensation and side effects",
    );
    expect("family" in scenario && scenario.family).toBe("workflow");

    const name = creator.deriveName(
      "Create a transactional workflow with compensation and side effects",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("workflow"),
    );
  });

  it("routes schema-evolution descriptions into a schema-evolution scaffold", async () => {
    const provider = makeMockProvider(mockSchemaEvolutionResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-schema-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create a schema evolution scenario with stale context after breaking field changes",
    );
    expect("family" in scenario && scenario.family).toBe("schema_evolution");

    const name = creator.deriveName(
      "Create a schema evolution scenario with stale context after breaking field changes",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("schema_evolution"),
    );
  });

  it("routes tool-fragility descriptions into a tool-fragility scaffold", async () => {
    const provider = makeMockProvider(mockToolFragilityResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-tool-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create a tool fragility scenario with API contract drift and environment changes",
    );
    expect("family" in scenario && scenario.family).toBe("tool_fragility");

    const name = creator.deriveName(
      "Create a tool fragility scenario with API contract drift and environment changes",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("tool_fragility"),
    );
  });

  it("routes negotiation descriptions into a negotiation scaffold", async () => {
    const provider = makeMockProvider(mockNegotiationResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-negotiation-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create a negotiation scenario with hidden BATNA, counteroffers, and adversarial preferences",
    );
    expect("family" in scenario && scenario.family).toBe("negotiation");

    const name = creator.deriveName(
      "Create a negotiation scenario with hidden BATNA, counteroffers, and adversarial preferences",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("negotiation"),
    );
  });

  it("routes operator-loop descriptions into an operator_loop scaffold (AC-432)", async () => {
    const provider = makeMockProvider(mockOperatorLoopResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-operator-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create an operator-in-the-loop scenario for support triage with escalation judgment",
    );
    expect("family" in scenario && scenario.family).toBe("operator_loop");
    expect("generatedSource" in scenario && typeof scenario.generatedSource).toBe("string");

    const name = creator.deriveName(
      "Create an operator-in-the-loop scenario for support triage with escalation judgment",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.js"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("operator_loop"),
    );
  });

  it("routes coordination descriptions into a coordination scaffold", async () => {
    const provider = makeMockProvider(mockCoordinationResponse());
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-coordination-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    const scenario = await creator.create(
      "Create a multi-agent coordination scenario with handoffs and partial context",
    );
    expect("family" in scenario && scenario.family).toBe("coordination");

    const name = creator.deriveName(
      "Create a multi-agent coordination scenario with handoffs and partial context",
    );
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "scenario.py"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("coordination"),
    );
  });

  it("rejects classified-but-unsupported game families", async () => {
    const provider = makeMockProvider(mockLlmResponse(SAMPLE_SPEC));
    const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-creator-game-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });

    expect(classifyScenarioFamily("Create a competitive two-player board game").familyName).toBe(
      "game",
    );
    await expect(creator.create("Create a competitive two-player board game")).rejects.toThrow(
      "not yet supported for custom scaffolding",
    );
  });

  it("classifies artifact-editing descriptions into the artifact_editing family", () => {
    expect(
      classifyScenarioFamily("Edit a YAML config file to add a database section").familyName,
    ).toBe("artifact_editing");
  });

  it("classifies investigation descriptions into the investigation family", () => {
    expect(
      classifyScenarioFamily(
        "Create an investigation where the agent gathers evidence and avoids red herrings",
      ).familyName,
    ).toBe("investigation");
  });

  it("classifies workflow descriptions into the workflow family", () => {
    expect(
      classifyScenarioFamily("Create a transactional workflow with compensation and side effects")
        .familyName,
    ).toBe("workflow");
  });

  it("classifies schema-evolution descriptions into the schema_evolution family", () => {
    expect(
      classifyScenarioFamily(
        "Create a schema evolution scenario with stale context after breaking field changes",
      ).familyName,
    ).toBe("schema_evolution");
  });

  it("classifies tool-fragility descriptions into the tool_fragility family", () => {
    expect(
      classifyScenarioFamily(
        "Create a tool fragility scenario with API contract drift and environment changes",
      ).familyName,
    ).toBe("tool_fragility");
  });

  it("classifies negotiation descriptions into the negotiation family", () => {
    expect(
      classifyScenarioFamily(
        "Create a negotiation scenario with hidden BATNA, counteroffers, and adversarial preferences",
      ).familyName,
    ).toBe("negotiation");
  });

  it("classifies operator-loop descriptions into the operator_loop family", () => {
    expect(
      classifyScenarioFamily(
        "Create an operator-in-the-loop scenario for support triage with escalation judgment",
      ).familyName,
    ).toBe("operator_loop");
  });

  it("classifies coordination descriptions into the coordination family", () => {
    expect(
      classifyScenarioFamily(
        "Create a multi-agent coordination scenario with handoffs and partial context",
      ).familyName,
    ).toBe("coordination");
  });
});

describe("sampleInput wiring", () => {
  it("embeds sampleInput in getTaskPrompt", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      taskPrompt: "Analyze the following data.",
      sampleInput: '{"users": [{"name": "Alice"}]}',
    };
    const task = createAgentTask({ spec, name: "data_test" });
    const prompt = task.getTaskPrompt({});
    expect(prompt).toContain("Analyze the following data");
    expect(prompt).toContain('{"users"');
  });

  it("includes sampleInput in initialState", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      sampleInput: "some data",
    };
    const task = createAgentTask({ spec, name: "data_test" });
    const state = task.initialState();
    expect(state.sampleInput).toBe("some data");
  });

  it("no sampleInput leaves prompt unchanged", () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "basic" });
    const prompt = task.getTaskPrompt({});
    expect(prompt).toBe(SAMPLE_SPEC.taskPrompt);
  });
});

describe("internalRetries surfacing", () => {
  it("AgentTaskResult accepts internalRetries", () => {
    const result = { score: 0.8, reasoning: "ok", dimensionScores: {}, internalRetries: 2 };
    const parsed = AgentTaskResultSchema.parse(result);
    expect(parsed.internalRetries).toBe(2);
  });

  it("AgentTaskResult defaults internalRetries to 0", () => {
    const result = { score: 0.8, reasoning: "ok" };
    const parsed = AgentTaskResultSchema.parse(result);
    expect(parsed.internalRetries).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// AC-306: reviseOutput must not pass empty model string to provider
// ---------------------------------------------------------------------------

describe("AC-306: factory reviseOutput model sanitization", () => {
  it("should pass undefined model to provider when judgeModel is empty", async () => {
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");

    let capturedModel: string | undefined;
    const mockProvider = {
      name: "mock",
      defaultModel: () => "default-model",
      complete: async (opts: any) => {
        capturedModel = opts.model;
        return { text: "revised output", model: "default-model", usage: {} };
      },
    };

    const spec = {
      taskPrompt: "Write something",
      judgeRubric: "Evaluate quality",
      judgeModel: "",
      outputFormat: "free_text",
      maxRounds: 3,
      qualityThreshold: 0.9,
      revisionPrompt: "Improve your response.",
    };

    const task = createAgentTask({
      spec: spec as any,
      name: "test_task",
      provider: mockProvider as any,
    });
    await task.reviseOutput(
      "original output",
      { score: 0.5, reasoning: "needs work", dimensionScores: {}, internalRetries: 0 },
      {},
    );

    // model should be undefined (not ""), so the provider uses its default
    expect(capturedModel).toBeUndefined();
  });

  it("should pass actual model when judgeModel is non-empty", async () => {
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");

    let capturedModel: string | undefined;
    const mockProvider = {
      name: "mock",
      defaultModel: () => "default-model",
      complete: async (opts: any) => {
        capturedModel = opts.model;
        return { text: "revised", model: "custom-model", usage: {} };
      },
    };

    const spec = {
      taskPrompt: "Write something",
      judgeRubric: "Evaluate",
      judgeModel: "custom-model",
      outputFormat: "free_text",
      maxRounds: 3,
      qualityThreshold: 0.9,
      revisionPrompt: "Improve.",
    };

    const task = createAgentTask({
      spec: spec as any,
      name: "test_task_2",
      provider: mockProvider as any,
    });
    await task.reviseOutput(
      "original",
      { score: 0.5, reasoning: "weak", dimensionScores: {}, internalRetries: 0 },
      {},
    );

    expect(capturedModel).toBe("custom-model");
  });
});
