import type { WorkflowSpec } from "./workflow-spec.js";
import { parseRawWorkflowSpec } from "./workflow-spec.js";
import {
  designFamilySpec,
  parseFamilyDesignerSpec,
  type FamilyDesignerDescriptor,
} from "./family-designer.js";

export const WORKFLOW_SPEC_START = "<!-- WORKFLOW_SPEC_START -->";
export const WORKFLOW_SPEC_END = "<!-- WORKFLOW_SPEC_END -->";

const WORKFLOW_DESCRIPTOR: FamilyDesignerDescriptor<WorkflowSpec> = {
  family: "workflow",
  startDelimiter: WORKFLOW_SPEC_START,
  endDelimiter: WORKFLOW_SPEC_END,
  missingDelimiterLabel: "WORKFLOW_SPEC",
  parseRaw: parseRawWorkflowSpec,
};

const EXAMPLE_SPEC = {
  description: "Execute an order-processing workflow with compensation when downstream steps fail.",
  environment_description: "Mock commerce workflow with payment, inventory, and notification side effects.",
  initial_state_description: "No order steps have run and no side effects have been produced.",
  workflow_steps: [
    {
      name: "charge_payment",
      description: "Charge the customer payment method.",
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
    {
      name: "send_confirmation",
      description: "Send the confirmation notification.",
      idempotent: true,
      reversible: false,
    },
  ],
  success_criteria: [
    "all required workflow steps complete in the correct order",
    "failed steps trigger compensation for reversible side effects",
  ],
  failure_modes: ["payment failure", "inventory reservation conflict", "notification sent before rollback"],
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
      description: "Send a confirmation notification.",
      parameters: { channel: "string" },
      preconditions: ["reserve_inventory"],
      effects: ["confirmation_sent"],
    },
  ],
};

export const WORKFLOW_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a transactional workflow task, produce a WorkflowSpec JSON.

Wrap the output in delimiters:
${WORKFLOW_SPEC_START}
{ ... }
${WORKFLOW_SPEC_END}

Schema:
{
  "description": "human readable workflow summary",
  "environment_description": "what system or business process is modeled",
  "initial_state_description": "starting state before steps run",
  "workflow_steps": [
    {
      "name": "snake_case_step",
      "description": "what the step does",
      "idempotent": true,
      "reversible": true,
      "compensation": "optional_compensation_step"
    }
  ],
  "success_criteria": ["criterion 1", "criterion 2"],
  "failure_modes": ["failure mode"],
  "max_steps": 7,
  "actions": [
    {
      "name": "snake_case_action",
      "description": "what the action does",
      "parameters": {"param": "type"},
      "preconditions": ["prior_action"],
      "effects": ["effect"]
    }
  ]
}

Rules:
- model the task as an explicit ordered workflow with transactional side effects
- include at least two workflow steps and one reversible step with compensation when appropriate
- keep action names aligned to workflow step names when possible
- include failure modes that require retry, rollback, or compensation
- make the task about executing the workflow, not writing prose about it

Example:
${WORKFLOW_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${WORKFLOW_SPEC_END}
`;

export function parseWorkflowSpec(text: string): WorkflowSpec {
  return parseFamilyDesignerSpec(text, WORKFLOW_DESCRIPTOR);
}

export async function designWorkflow(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<WorkflowSpec> {
  return designFamilySpec(
    description,
    WORKFLOW_DESIGNER_SYSTEM,
    WORKFLOW_DESCRIPTOR,
    llmFn,
  );
}
