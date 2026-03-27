import type { InvestigationSpec } from "./investigation-spec.js";
import { parseRawInvestigationSpec } from "./investigation-spec.js";
import { healSpec } from "./spec-auto-heal.js";

export const INVESTIGATION_SPEC_START = "<!-- INVESTIGATION_SPEC_START -->";
export const INVESTIGATION_SPEC_END = "<!-- INVESTIGATION_SPEC_END -->";

const EXAMPLE_SPEC = {
  description: "Investigate a production outage by gathering evidence and identifying the root cause.",
  environment_description: "Mock service environment with logs, dashboards, and deployment metadata.",
  initial_state_description: "An API outage has started and only partial evidence is visible.",
  evidence_pool_description:
    "Service logs implicate the auth service, dashboard metrics show latency spikes, and an unrelated cron job log is a red herring.",
  diagnosis_target: "A bad auth-service deployment exhausted the database connection pool.",
  success_criteria: [
    "collect enough evidence to explain the outage",
    "identify the correct diagnosis without relying on red herrings",
  ],
  failure_modes: ["following a cron-job red herring", "stopping before enough evidence is collected"],
  max_steps: 6,
  actions: [
    {
      name: "inspect_logs",
      description: "Review service logs around the incident window.",
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
      description: "Submit the final diagnosis grounded in collected evidence.",
      parameters: { diagnosis: "string" },
      preconditions: ["inspect_logs", "query_metrics"],
      effects: ["diagnosis_recorded"],
    },
  ],
};

export const INVESTIGATION_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for an investigation or debugging task, produce an InvestigationSpec JSON.

Wrap the output in delimiters:
${INVESTIGATION_SPEC_START}
{ ... }
${INVESTIGATION_SPEC_END}

Schema:
{
  "description": "human readable investigation summary",
  "environment_description": "what environment or system is being investigated",
  "initial_state_description": "starting state and visible symptoms",
  "evidence_pool_description": "what evidence exists, including any red herrings",
  "diagnosis_target": "the correct root cause or diagnosis",
  "success_criteria": ["criterion 1", "criterion 2"],
  "failure_modes": ["failure mode"],
  "max_steps": 6,
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
- model the task around gathering evidence and reaching a diagnosis, not writing an essay about debugging
- include one explicit diagnosis target and mention at least one red herring in the evidence pool description
- make action names short and snake_case
- include at least two success criteria and at least two actions
- reserve one action for recording or submitting the diagnosis

Example:
${INVESTIGATION_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${INVESTIGATION_SPEC_END}
`;

export function parseInvestigationSpec(text: string): InvestigationSpec {
  const startIdx = text.indexOf(INVESTIGATION_SPEC_START);
  const endIdx = text.indexOf(INVESTIGATION_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain INVESTIGATION_SPEC delimiters");
  }
  const raw = text.slice(startIdx + INVESTIGATION_SPEC_START.length, endIdx).trim();
  return parseRawInvestigationSpec(
    healSpec(JSON.parse(raw) as Record<string, unknown>, "investigation"),
  );
}

export async function designInvestigation(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<InvestigationSpec> {
  return parseInvestigationSpec(
    await llmFn(INVESTIGATION_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
